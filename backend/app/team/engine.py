"""Multi-Model Team engine — the flagship feature.

Workflow: TASK → PLANNING → MASTER PLAN → ROLE ASSIGNMENT → EXECUTION →
REVIEW → TEST → FIX → FINAL REVIEW → DELIVERY.

Design notes
------------
* 2–4 local models participate; they are invoked **sequentially** because
  a single GPU can serve one model at a time (RTX 4060 Ti 8GB).
* Every member call passes CostGuard and is metered into ``usage_events``
  with ``team_id``/``team_member_id`` → per-model + TEAM TOTAL tokens.
* The shared state (task, master plan, decisions, work products, findings,
  errors, board) lives in ``teams``/``team_members``/``team_events``/
  ``team_tasks`` and is fully re-readable via ``GET /api/team/{id}``.
* Only *decisions, actions, findings, tool-independent work products,
  results, errors and status* are surfaced. Chain-of-thought is never
  exposed.
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, AsyncIterator

from ..core.errors import BadRequest
from ..db.repo import ModelsRepo, TeamsRepo, UsageRepo
from ..providers.base import ChatMessage
from ..services.model_runner import ModelRunner, UsageSink
from ..services.settings_service import SettingsService

log = logging.getLogger("aicc.team")

MAX_RESULT_CHARS = 5000
MAX_MEMBERS = 4
MIN_MEMBERS = 2

ANALYSIS_FORMAT = """Analyze this task. Reply with ONLY these labeled sections (keep each 2-5 lines):
REQUIREMENTS:
ARCHITECTURE:
RISKS:
DEPENDENCIES:
SUBTASKS:
TESTING:
TOOLS:
"""

REVIEW_FORMAT = """Review the work product sections assigned to you. Reply with ONLY:
VERDICT: APPROVE  (if everything is solid)
or
VERDICT: REVISE
FINDINGS:
- finding 1 (specific, actionable, references the relevant section)
- finding 2 ...
(no prose before or after; findings only when VERDICT is REVISE)
"""


def _trim(text: str, limit: int = MAX_RESULT_CHARS) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit] + "\n… [truncated]"


def _task_lines(text: str) -> list[str]:
    """Extract task titles from a plan: bullets or numbered lines."""
    out: list[str] = []
    for line in text.splitlines():
        s = line.strip().lstrip("-*•\t")
        s = re.sub(r"^\d+[.)]\s*", "", s).strip()
        if not s:
            continue
        if len(s) >= 4 and not s.startswith(("REQUIREMENTS", "ARCHITECTURE", "RISKS",
                                             "DEPENDENCIES", "SUBTASKS", "TESTING",
                                             "TOOLS", "VERDICT", "FINDINGS")):
            out.append(s[:160])
        if len(out) >= 12:
            break
    return out


class TeamEngine:
    def __init__(self, *, runner: ModelRunner, settings: SettingsService,
                 teams: TeamsRepo, models: ModelsRepo, usage: UsageRepo):
        self.runner = runner
        self.settings = settings
        self.teams = teams
        self.models = models
        self.usage = usage

    # ── role assignment ──────────────────────────────────────────────
    async def _assign_roles(self, team_id: int, model_names: list[str],
                            overrides: dict[str, str] | None) -> list[dict]:
        rows = {}
        for name in model_names:
            row = await self.models.get("ollama", name)
            if row is None:
                row = {"capabilities_json": "[]", "categories_json": "[]",
                       "name": name}
            rows[name] = row
        import json
        scores: dict[str, dict[str, int]] = {}
        for name in model_names:
            cats = {str(c) for c in json.loads(rows[name].get("categories_json") or "[]")}
            caps = {str(c) for c in json.loads(rows[name].get("capabilities_json") or "[]")}
            s = {"architect": 0, "developer": 0, "qa": 0, "documentation": 0,
                 "researcher": 0}
            if "reasoning" in cats:
                s["architect"] += 2
            if "coding" in cats or "tools" in caps:
                s["developer"] += 2
            if "research" in cats:
                s["researcher"] += 2
            if "creative" in cats:
                s["documentation"] += 1
            if "fast" in cats:
                s["qa"] += 1
            if "vision" in cats:
                s["documentation"] += 1
            # tie-breakers: stable
            s["architect"] += 1
            scores[name] = s

        role_pool = ["architect", "developer", "qa", "documentation", "researcher"]
        assigned: dict[str, list[str]] = {name: [] for name in model_names}
        # manual overrides first
        for name, role in (overrides or {}).items():
            if name in assigned and role:
                assigned[name] = [r.strip().lower() for r in re.split(r"[,/]", role) if r.strip()]
        # greedy fill remaining roles
        used_roles = {r for roles in assigned.values() for r in roles}
        for role in role_pool:
            if role in used_roles:
                continue
            best = max(model_names,
                       key=lambda n: (scores[n].get(role, 0), -model_names.index(n)))
            if scores[best].get(role, 0) > 0:
                assigned[best].append(role)
        # every member must have at least one role
        for name in model_names:
            if not assigned[name]:
                scores_local = {r: s for r, s in scores[name].items()}
                top = max(scores_local, key=scores_local.get)
                assigned[name].append(top)
        return assigned

    # ── main flow ────────────────────────────────────────────────────
    async def run(self, *, task: str, model_names: list[str],
                  provider_name: str | None = "ollama",
                  roles_override: dict[str, str] | None = None,
                  project_id: int | None = None,
                  cancel: asyncio.Event | None = None) -> AsyncIterator[dict[str, Any]]:
        cancel = cancel or asyncio.Event()
        task = task.strip()
        if not task:
            raise BadRequest("Task must not be empty.")
        raw_models = [m for m in dict.fromkeys(model_names) if m]
        if not (MIN_MEMBERS <= len(raw_models) <= MAX_MEMBERS):
            raise BadRequest(f"Team requires {MIN_MEMBERS}–{MAX_MEMBERS} distinct "
                             f"models (got {len(raw_models)}).", code="INVALID_TEAM_SIZE")
        model_names = raw_models[:MAX_MEMBERS]

        rt = await self.settings.as_dict()
        max_review_rounds = int(rt.get("team_max_rounds", 2))
        team = await self.teams.create(name="Team: " + task[:40], task=task,
                                       status="planning", project_id=project_id)
        sid = team["id"]
        sinks: dict[str, UsageSink] = {m: UsageSink(team_id=sid, team_member_id=None)
                                       for m in model_names}

        yield {"type": "team", "team_id": sid, "members": model_names,
               "project_id": project_id}
        await self.teams.add_event(sid, "planning", "status",
                                   f"Team created with {len(model_names)} models: "
                                   + ", ".join(model_names))
        yield {"type": "phase", "team_id": sid, "phase": "planning", "status": "running"}

        try:
            # ── PLANNING: every model analyzes ──
            analyses: dict[str, str] = {}
            for name in model_names:
                if cancel.is_set():
                    break
                member = await self.teams.add_member(sid, name, provider_name,
                                                     role="member", responsibility="")
                sinks[name].team_member_id = member["id"]
                await self.teams.update_member(member["id"], status="planning")
                gen = await self._call(name, provider_name,
                                       self.runner.messages(
                                           ("system", "You are a senior engineer on "
                                                      "a multi-model team."),
                                           ("user", ANALYSIS_FORMAT + "\nTASK:\n" + task)),
                                       sinks[name])
                await self.teams.add_tokens(member["id"], gen.input_tokens or 0,
                                            gen.output_tokens or 0)
                await self.teams.update_member(member["id"], status="idle")
                if gen.status == "error":
                    await self.teams.add_event(sid, "planning", "error",
                                               f"{name}: model call failed: {gen.error}",
                                               actor=name)
                    yield {"type": "activity", "team_id": sid, "phase": "planning",
                           "actor": name, "kind": "error", "content": gen.error}
                    analyses[name] = f"(analysis unavailable — model error: {gen.error})"
                else:
                    analyses[name] = _trim(gen.text, 1200)
                    await self.teams.add_event(sid, "planning", "analysis",
                                               _trim(gen.text, 800), actor=name)
                    yield {"type": "activity", "team_id": sid, "phase": "planning",
                           "actor": name, "kind": "analysis",
                           "content": _trim(gen.text, 400)}

            if cancel.is_set():
                await self._finish_cancelled(sid)
                return

            # ── MASTER PLAN (orchestrator = first model) ──
            orchestrator = model_names[0]
            plan_prompt = (f"The team analyzed this task:\n\n{task}\n\n"
                           "Team analyses:\n" + "\n\n".join(
                               f"### {n}\n{a}" for n, a in analyses.items()) +
                           "\n\nCreate the MASTER PLAN: a numbered sequence of phases "
                           "(1. requirements 2. design 3. implementation 4. testing "
                           "5. review 6. delivery) each with 2-4 concrete subtasks. "
                           "Reply with the plan only.")
            plan_gen = await self._call(orchestrator, provider_name,
                                        self.runner.messages(
                                            ("system", "You are the team architect and "
                                                       "coordinator. Produce concrete, "
                                                       "executable plans."),
                                            ("user", plan_prompt)),
                                        sinks[orchestrator])
            master_plan = _trim(plan_gen.text, 6000) if plan_gen.status != "error" \
                else "(master plan synthesis failed — using task directly)"
            await self.teams.update(sid, master_plan=master_plan, status="planning")
            await self.teams.add_event(sid, "planning", "plan", _trim(master_plan, 2000),
                                       actor=orchestrator)
            yield {"type": "phase", "team_id": sid, "phase": "master_plan",
                   "status": "complete", "content": master_plan}

            # ── ROLE ASSIGNMENT ──
            assigned = await self._assign_roles(sid, model_names, roles_override)
            for name in model_names:
                roles = assigned[name]
                await self.teams.update_member(
                    (await self._member_id(sid, name)), role=", ".join(roles),
                    responsibility=", ".join(r.capitalize() for r in roles))
                await self.teams.add_event(sid, "roles", "decision",
                                           f"{name} → {', '.join(roles)}", actor=name)
                yield {"type": "activity", "team_id": sid, "phase": "roles",
                       "actor": name, "kind": "decision",
                       "content": f"role: {', '.join(roles)}"}

            # ── TASK BOARD ──
            task_titles = _task_lines(master_plan)
            if not task_titles:
                task_titles = ["Implement the task", "Test the result", "Review the result"]
            task_ids: list[int] = []
            for title in task_titles:
                assignee = self._assignee_for(title, assigned, model_names)
                t = await self.teams.add_task(sid, title, assignee=assignee)
                task_ids.append(t["id"])
                await self.teams.add_event(sid, "execution", "status",
                                           f"board: {title} → {assignee}")

            # ── EXECUTION: each member produces its work product ──
            await self.teams.update(sid, status="executing")
            await self.teams.add_event(sid, "execution", "status", "execution started")
            yield {"type": "phase", "team_id": sid, "phase": "execution",
                   "status": "running"}
            products: dict[str, str] = {}
            for name in model_names:
                if cancel.is_set():
                    break
                member_id = await self._member_id(sid, name)
                roles = assigned[name]
                await self.teams.update_member(member_id, status="working")
                # mark tasks
                for task_id in task_ids:
                    trow = await self._task(sid, task_id)
                    if (trow or {}).get("assignee") == name and trow["status"] == "todo":
                        await self.teams.update_task(task_id, status="in_progress")
                product = await self._produce(name, provider_name, task, master_plan,
                                              roles, analyses, sinks[name])
                await self.teams.update_member(member_id, status="idle")
                products[name] = product
                await self.teams.add_event(sid, "execution", "action",
                                           _trim(product, 1500), actor=name)
                yield {"type": "activity", "team_id": sid, "phase": "execution",
                       "actor": name, "kind": "action", "content": _trim(product, 500)}
                for task_id in task_ids:
                    trow = await self._task(sid, task_id)
                    if (trow or {}).get("assignee") == name:
                        await self.teams.update_task(task_id, status="review",
                                                     progress=100)

            if cancel.is_set():
                await self._finish_cancelled(sid)
                return

            # ── REVIEW / TEST / FIX loop ──
            await self.teams.update(sid, status="review")
            yield {"type": "phase", "team_id": sid, "phase": "review",
                   "status": "running"}
            findings: dict[str, list[str]] = {}
            round_no = 0
            while round_no < max_review_rounds:
                round_no += 1
                findings = {name: [] for name in model_names}
                reviewers = [m for m in model_names if m != orchestrator] or model_names
                for reviewer in reviewers:
                    await self.teams.add_event(sid, "review", "status",
                                               f"{reviewer} reviewing (round {round_no})",
                                               actor=reviewer)
                    review = await self._review(reviewer, provider_name, task,
                                                master_plan, products,
                                                sinks[reviewer])
                    verdict = "APPROVE" if review.get("verdict", "APPROVE") == "APPROVE" \
                        else "REVISE"
                    await self.teams.add_event(
                        sid, "review", "review",
                        f"[{reviewer}] {verdict}: " + "\n".join(
                            f"- {f}" for f in review["findings"])[:2500], actor=reviewer)
                    yield {"type": "activity", "team_id": sid, "phase": "review",
                           "actor": reviewer, "kind": "review",
                           "content": verdict + (": " + "; ".join(review["findings"])[:300]
                                                 if review["findings"] else "")}
                    if review["findings"]:
                        # attribute findings to the most plausible owner
                        owner = self._owner_for(reviewer, findings, review["findings"])
                        findings[owner].extend(review["findings"])

                if all(not v for v in findings.values()):
                    await self.teams.add_event(sid, "review", "status",
                                               "all reviews approved")
                    break
                await self.teams.update(sid, status="fixing")
                yield {"type": "phase", "team_id": sid, "phase": "fix",
                       "status": "running", "round": round_no}
                for name in model_names:
                    if not findings.get(name):
                        continue
                    revised = await self._fix(name, provider_name, task,
                                              master_plan, products[name],
                                              findings[name], sinks[name])
                    products[name] = revised
                    await self.teams.add_event(sid, "fix", "action",
                                               _trim(revised, 1500), actor=name)
                    yield {"type": "activity", "team_id": sid, "phase": "fix",
                           "actor": name, "kind": "action",
                           "content": _trim(revised, 400)}
                for task_id in task_ids:
                    trow = await self._task(sid, task_id)
                    if (trow or {}).get("status") == "review":
                        await self.teams.update_task(task_id, status="done", progress=100)

            # ── FINAL REVIEW + DELIVERY ──
            await self.teams.update(sid, status="final")
            yield {"type": "phase", "team_id": sid, "phase": "final_review",
                   "status": "running"}
            deliverable = await self._compose(orchestrator, provider_name, task,
                                              master_plan, products, findings,
                                              sinks[orchestrator])
            await self.teams.update(sid, deliverable=deliverable,
                                    status="delivered", master_plan=master_plan)
            for task_id in task_ids:
                trow = await self._task(sid, task_id)
                if (trow or {}).get("status") not in ("done",):
                    await self.teams.update_task(task_id, status="done", progress=100)
            await self.teams.add_event(sid, "delivery", "deliverable",
                                       _trim(deliverable, 3000), actor=orchestrator)
            yield {"type": "tokens", "team_id": sid,
                   "members": await self._token_rows(sid),
                   "total": await self.teams.token_totals(sid)}
            yield {"type": "done", "team_id": sid, "status": "delivered",
                   "deliverable": deliverable}
        except Exception as exc:
            log.exception("team run %s failed", sid)
            try:
                await self.teams.update(sid, status="error")
                await self.teams.add_event(sid, "error", "error",
                                           getattr(exc, "message", str(exc)))
            except Exception:  # pragma: no cover
                pass
            yield {"type": "error", "team_id": sid, "code": "INTERNAL_ERROR",
                   "message": getattr(exc, "message", str(exc))}

    # ── helpers ──────────────────────────────────────────────────────
    async def _call(self, model: str, provider: str | None, messages: list[ChatMessage],
                    sink: UsageSink):
        return await self.runner.generate(messages=messages, provider_name=provider,
                                          model_name=model, sink=sink)

    async def _member_id(self, team_id: int, name: str) -> int:
        members = await self.teams.members(team_id)
        for m in members:
            if m["model"] == name:
                return m["id"]
        raise BadRequest(f"member {name} not found", code="MEMBER_NOT_FOUND")

    async def _task(self, team_id: int, task_id: int) -> dict | None:
        for t in await self.teams.tasks(team_id):
            if t["id"] == task_id:
                return t
        return None

    def _assignee_for(self, title: str, assigned: dict[str, list[str]],
                      model_names: list[str]) -> str:
        low = title.lower()
        if any(k in low for k in ("test", "qa", "verify", "check")):
            for n in model_names:
                if "qa" in assigned[n]:
                    return n
        if any(k in low for k in ("doc", "report", "summary", "readme")):
            for n in model_names:
                if "documentation" in assigned[n]:
                    return n
        if any(k in low for k in ("architect", "design", "requirement")):
            for n in model_names:
                if "architect" in assigned[n]:
                    return n
        for n in model_names:
            if "developer" in assigned[n]:
                return n
        return model_names[0]

    def _owner_for(self, reviewer: str, findings: dict[str, list[str]],
                   new_findings: list[str]) -> str:
        """Attribute findings to someone other than the reviewer (round-robin)."""
        others = [m for m in findings if m != reviewer]
        if not others:
            return reviewer
        counts = {m: len(v) for m, v in findings.items() if m in others}
        return min(others, key=lambda m: counts[m])

    async def _produce(self, name: str, provider: str | None, task: str,
                       master_plan: str, roles: list[str], analyses: dict[str, str],
                       sink: UsageSink) -> str:
        prompt = (f"TASK:\n{task}\n\nMASTER PLAN:\n{master_plan}\n\n"
                  f"YOUR ROLE: {', '.join(roles)}\n\n"
                  "Produce YOUR work product for this task: the concrete content "
                  "your role is responsible for (design decisions, code with "
                  "```language fences, tests, review criteria, docs). Be specific and "
                  "complete; this is shared with the team. Reply with your work "
                  "product only.")
        gen = await self._call(name, provider,
                               self.runner.messages(
                                   ("system", "You are a specialist on a multi-model "
                                              "team. Deliver concrete work products."),
                                   ("user", prompt)), sink)
        return _trim(gen.text, MAX_RESULT_CHARS) if gen.status != "error" \
            else f"(work product failed: {gen.error})"

    async def _review(self, reviewer: str, provider: str | None, task: str,
                      master_plan: str, products: dict[str, str],
                      sink: UsageSink) -> dict[str, Any]:
        sections = "\n\n".join(f"### {n}\n{p}" for n, p in products.items())
        prompt = (f"TASK:\n{task}\n\nMASTER PLAN:\n{master_plan}\n\n"
                  f"WORK PRODUCTS:\n{sections}\n\n" + REVIEW_FORMAT)
        gen = await self._call(reviewer, provider,
                               self.runner.messages(
                                   ("system", "You are a rigorous QA engineer. Review "
                                              "team work and answer with the strict "
                                              "VERDICT format."),
                                   ("user", prompt)), sink)
        text = gen.text or ""
        verdict = "APPROVE" if "REVISE" not in text.upper().split("VERDICT", 1)[-1][:30] \
            else "REVISE"
        findings = [l.strip().lstrip("-*").strip()
                    for l in text.splitlines()
                    if l.strip().startswith(("-", "*")) and len(l.strip()) > 3]
        return {"verdict": verdict, "findings": findings[:10]}

    async def _fix(self, name: str, provider: str | None, task: str,
                   master_plan: str, product: str, findings: list[str],
                   sink: UsageSink) -> str:
        prompt = (f"TASK:\n{task}\nMASTER PLAN:\n{master_plan}\n\n"
                  f"YOUR CURRENT WORK PRODUCT:\n{product}\n\n"
                  "Review findings to address:\n" + "\n".join(
                      f"- {f}" for f in findings) +
                  "\n\nReturn the REVISED work product (full, updated version) only.")
        gen = await self._call(name, provider,
                               self.runner.messages(
                                   ("system", "You are a specialist revising your work "
                                              "based on review findings."),
                                   ("user", prompt)), sink)
        return _trim(gen.text, MAX_RESULT_CHARS) if gen.status != "error" \
            else product

    async def _compose(self, orchestrator: str, provider: str | None, task: str,
                       master_plan: str, products: dict[str, str],
                       findings: dict[str, list[str]], sink: UsageSink) -> str:
        prompt = (f"TASK:\n{task}\n\nMASTER PLAN:\n{master_plan}\n\n"
                  "FINAL WORK PRODUCTS:\n" + "\n\n".join(
                      f"### {n}\n{p}" for n, p in products.items()) +
                  "\n\nCompose the final deliverable: one complete, well-structured "
                  "document (markdown, with code blocks) that a user can execute. "
                  "Include a short summary, decisions, implementation, tests and "
                  "verification status. This is the delivery. Reply with the "
                  "deliverable only.")
        gen = await self._call(orchestrator, provider,
                               self.runner.messages(
                                   ("system", "You are the team lead composing the "
                                              "final deliverable."),
                                   ("user", prompt)), sink)
        return _trim(gen.text, MAX_RESULT_CHARS) if gen.status != "error" \
            else "\n\n".join(f"### {n}\n{p}" for n, p in products.items())

    async def _token_rows(self, team_id: int) -> list[dict]:
        members = await self.teams.members(team_id)
        return [{"model": m["model"], "role": m["role"],
                 "input_tokens": m["input_tokens"],
                 "output_tokens": m["output_tokens"],
                 "total_tokens": m["input_tokens"] + m["output_tokens"]}
                for m in members]

    async def _finish_cancelled(self, team_id: int) -> None:
        await self.teams.update(team_id, status="cancelled")
        await self.teams.add_event(team_id, "cancelled", "status",
                                   "Team run cancelled by user.")
