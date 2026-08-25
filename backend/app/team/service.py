"""Team Mode (P5) — a small model pipeline around the agent engine.

Pipeline (STRICTLY SEQUENTIAL — VRAM-safe by construction: at most one
model resident at any moment, and the executor turns are full agent runs
with the complete approval/audit gateway):
  planners (0..n) → executor (1, tool-enabled agent run) → reviewers (0..n)
  → if the final reviewer requests changes, ONE revision executor run
  incorporating the review feedback (never an unbounded loop).

Token honesty: planner/reviewer turns record usage with team/member
attribution; executor turns keep their detailed usage inside the agent
run's own ledger (linked via team_runs.executor_run_id) and roll up into
team-level totals from the run row — never double-counted.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, AsyncIterator

from ..core.errors import BadRequest, NotFound
from ..db.repo import AgentRunsRepo, TeamRunsRepo, TeamsRepo, UsageRepo
from ..providers.base import ChatMessage, ChatOptions
from ..providers.registry import ProviderRegistry
from ..services.cost_guard import CostGuard
from ..services.model_router import ModelRouter
from ..services.settings_service import SettingsService
from .prompts import (build_executor_task, build_planner_prompt,
                      build_reviewer_prompt, parse_verdict)

log = logging.getLogger("aicc.team")

ROLES = ("planner", "executor", "reviewer")


class TeamRunManager:
    """Cooperative cancellation for team runs."""

    def __init__(self):
        self._cancels: dict[str, asyncio.Event] = {}

    def start(self, run_id: str) -> asyncio.Event:
        ev = asyncio.Event()
        self._cancels[run_id] = ev
        return ev

    def finish(self, run_id: str) -> None:
        self._cancels.pop(run_id, None)

    def stop(self, run_id: str) -> bool:
        ev = self._cancels.get(run_id)
        if ev is None:
            return False
        ev.set()
        return True


class TeamService:
    def __init__(self, *, teams: TeamsRepo, team_runs: TeamRunsRepo,
                 agent_runs: AgentRunsRepo, usage: UsageRepo,
                 registry: ProviderRegistry, router: ModelRouter,
                 guard: CostGuard, settings: SettingsService, agent_engine,
                 run_manager: TeamRunManager):
        self.teams = teams
        self.team_runs = team_runs
        self.agent_runs = agent_runs
        self.usage = usage
        self.registry = registry
        self.router = router
        self.guard = guard
        self.settings = settings
        self.agent_engine = agent_engine
        self.run_manager = run_manager
        # team_run_id → in-flight executor agent_run_id (for stop propagation)
        self._active_executor: dict[str, str] = {}

    def stop_run(self, run_id: str) -> bool:
        ok = self.run_manager.stop(run_id)
        er = self._active_executor.get(run_id)
        if er:
            self.agent_engine.runs_manager.stop(er)  # nested run has its own cancel
        return ok

    # ── team definitions ─────────────────────────────────────────────
    async def create_team(self, name: str, members: list[dict]) -> dict:
        name = (name or "").strip()
        if not name:
            raise BadRequest("Team name must not be empty.")
        if not 2 <= len(members) <= 4:
            raise BadRequest("A team needs 2–4 members.", code="TEAM_SIZE")
        roles = [m.get("role") for m in members]
        for r in roles:
            if r not in ROLES:
                raise BadRequest(f"Unknown role '{r}'. Allowed: {', '.join(ROLES)}.",
                                 code="TEAM_ROLE")
        if roles.count("executor") != 1:
            raise BadRequest("A team must have exactly one executor — it runs the "
                             "tool-enabled agent loop.", code="TEAM_EXECUTOR")
        if "planner" not in roles and "reviewer" not in roles:
            raise BadRequest("Add at least one planner or reviewer around the executor.",
                             code="TEAM_ROLE")
        cleaned = []
        for m in members:
            model = (m.get("model") or "").strip()
            if not model:
                raise BadRequest("Every member needs a model.", code="TEAM_MODEL")
            provider = (m.get("provider") or "").strip() or None
            if provider and provider not in self.registry.names():
                raise BadRequest(f"Unknown provider '{provider}'.", code="PROVIDER_NOT_FOUND")
            cleaned.append({"role": m["role"], "model": model, "provider": provider,
                            "responsibility": (m.get("responsibility") or "").strip()[:1000]})
        return await self.teams.create(name, cleaned)

    async def get_team(self, tid: int) -> dict:
        team = await self.teams.get(tid)
        if team is None:
            raise NotFound(f"Team {tid} not found.", code="TEAM_NOT_FOUND")
        team["members"] = await self.teams.members_of(tid)
        return team

    # ── run ──────────────────────────────────────────────────────────
    async def stream_run(self, *, team_id: int, task: str) -> AsyncIterator[dict[str, Any]]:
        task = task.strip()
        if not task:
            raise BadRequest("Team task must not be empty.")
        team = await self.get_team(team_id)
        members: list[dict] = team["members"]

        run = await self.team_runs.create(team_id, task)
        run_id = run["id"]
        cancel = self.run_manager.start(run_id)
        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        worker = asyncio.create_task(self._worker(
            queue=queue, run_id=run_id, team=team, members=members,
            task=task, cancel=cancel))
        try:
            while True:
                ev = await queue.get()
                if ev is None:
                    break
                yield ev
        finally:
            if not worker.done():
                worker.cancel()
                try:
                    await worker
                except asyncio.CancelledError:
                    pass
            self.run_manager.finish(run_id)

    async def _worker(self, *, queue: asyncio.Queue, run_id: str, team: dict,
                      members: list[dict], task: str, cancel: asyncio.Event) -> None:
        t0 = time.monotonic()
        status = "complete"
        error_text: str | None = None
        plan_text, result_text, review_text = "", "", ""
        verdict: str | None = None
        revision_used = 0
        executor_run_id: str | None = None
        team_in = team_out = 0

        async def emit(ev: dict[str, Any]) -> None:
            await queue.put(ev)

        async def run_executor(member: dict, idx: int, feedback: str) -> None:
            """One executor turn = a complete agent run (gateway, approvals, audit)."""
            nonlocal executor_run_id, result_text, team_in, team_out, status, error_text
            exec_task = build_executor_task(task=task, plan=plan_text,
                                            review_feedback=feedback)
            await emit({"type": "member_start", "index": idx, "role": "executor",
                        "model": member["model"], "provider": member["provider"],
                        "responsibility": member["responsibility"]})
            inner = self.agent_engine.stream_run(
                task=exec_task, provider_name=member["provider"] or None,
                model_name=member["model"])
            exec_status = "error"
            exec_error: str | None = None
            try:
                async for ev in inner:
                    etype = ev.get("type")
                    if etype == "meta":
                        executor_run_id = ev["run_id"]
                        self._active_executor[run_id] = ev["run_id"]
                        await self.team_runs.set_executor_run(run_id, ev["run_id"])
                        if cancel.is_set():
                            self.agent_engine.runs_manager.stop(ev["run_id"])
                    elif etype == "done":
                        exec_status = ev.get("status") or "error"
                        exec_error = ev.get("error")
                        row = await self.agent_runs.get(ev["run_id"]) or {}
                        i2 = int(row.get("input_tokens") or 0)
                        o2 = int(row.get("output_tokens") or 0)
                        team_in += i2
                        team_out += o2
                        await self.teams.add_member_tokens(member["id"], i2, o2)
                        if exec_status == "complete":
                            result_text = ev.get("result") or ""
                        await emit({"type": "member_done", "index": idx,
                                    "role": "executor", "status": exec_status,
                                    "input_tokens": i2, "output_tokens": o2,
                                    "result": (ev.get("result") or "")[:2000],
                                    "error": exec_error})
                        break
                    # every engine event streams through, tagged with the member
                    if etype != "done":
                        await emit({"type": "member_event", "index": idx, "event": ev})
            finally:
                self._active_executor.pop(run_id, None)
                await inner.aclose()
            if exec_status != "complete":
                status = exec_status  # stopped / denied / error propagate 1:1
                if exec_status == "error":
                    error_text = (f"Executor failed: {exec_error}"
                                  if exec_error else "Executor member failed.")

        try:
            await emit({"type": "team_meta", "run_id": run_id, "team_id": team["id"],
                        "team": team["name"], "members": [
                            {"index": i, "role": m["role"], "model": m["model"],
                             "provider": m["provider"], "responsibility": m["responsibility"]}
                            for i, m in enumerate(members)]})

            planners = [(i, m) for i, m in enumerate(members) if m["role"] == "planner"]
            (exec_idx, exec_member), = (
                (i, m) for i, m in enumerate(members) if m["role"] == "executor")
            reviewers = [(i, m) for i, m in enumerate(members) if m["role"] == "reviewer"]

            # ── planning stage ──
            for idx, member in planners:
                if cancel.is_set():
                    status = "stopped"
                    break
                text, i_tok, o_tok = await self._plain_turn(
                    idx=idx, member=member,
                    prompt=build_planner_prompt(
                        responsibility=member["responsibility"], task=task,
                        prior_plan=plan_text),
                    emit=emit, cancel=cancel)
                team_in += i_tok
                team_out += o_tok
                if text:
                    plan_text = text

            # ── executor (+ optional single revision) + review stage ──
            feedback = ""
            while status == "complete":
                if cancel.is_set():
                    status = "stopped"
                    break
                await run_executor(exec_member, exec_idx, feedback)
                if status != "complete":
                    break

                for idx, member in reviewers:
                    if cancel.is_set():
                        status = "stopped"
                        break
                    text_r, i2, o2 = await self._plain_turn(
                        idx=idx, member=member,
                        prompt=build_reviewer_prompt(
                            responsibility=member["responsibility"], task=task,
                            plan=plan_text, result=result_text),
                        emit=emit, cancel=cancel)
                    team_in += i2
                    team_out += o2
                    if text_r:
                        review_text = text_r
                if status != "complete":
                    break

                verdict = parse_verdict(review_text)
                await emit({"type": "verdict", "verdict": verdict,
                            "message": ("Reviewer accepted the result."
                                        if verdict == "accepted"
                                        else "Reviewer requested changes."
                                        if verdict == "changes_requested"
                                        else "Reviewer gave no verdict — accepting.")})
                if verdict == "changes_requested" and revision_used == 0 and reviewers:
                    revision_used = 1
                    feedback = review_text
                    await emit({"type": "note", "level": "info",
                                "message": "Starting the single allowed revision run "
                                           "with the reviewer feedback."})
                    continue
                break

        except asyncio.CancelledError:
            status = "stopped"       # still finish the run row honestly
        except Exception as exc:
            status = "error"
            error_text = getattr(exc, "message", str(exc))
            log.warning("team run %s failed: %s", run_id, error_text)

        elapsed = round(time.monotonic() - t0, 1)
        await self.team_runs.finish(
            run_id, status=status, plan=plan_text, result=result_text,
            review=review_text, verdict=verdict, revision_used=revision_used,
            executor_run_id=executor_run_id, in_tok=team_in, out_tok=team_out,
            error=error_text)
        await emit({"type": "usage", "input_tokens": team_in,
                    "output_tokens": team_out, "elapsed_s": elapsed})
        if status == "error" and error_text:
            await emit({"type": "error", "code": "TEAM_ERROR", "message": error_text})
        await emit({"type": "team_done", "run_id": run_id, "status": status,
                    "plan": plan_text, "result": result_text, "review": review_text,
                    "verdict": verdict, "revision_used": revision_used,
                    "executor_run_id": executor_run_id, "error": error_text,
                    "elapsed_s": elapsed})
        await queue.put(None)

    async def _plain_turn(self, *, idx: int, member: dict, prompt: str, emit,
                          cancel: asyncio.Event) -> tuple[str, int, int]:
        """Planner/reviewer turn: streamed text, guarded + metered, no tools."""
        await emit({"type": "member_start", "index": idx, "role": member["role"],
                    "model": member["model"], "provider": member["provider"],
                    "responsibility": member["responsibility"]})
        provider, model, row = await self.router.resolve(
            member["provider"] or None, member["model"])
        totals = await self.usage.totals()
        await self.guard.guard_request(provider, model, row,
                                       total_spent_eur=totals["cost_eur"])
        keep_alive = await self.settings.get_typed("keep_alive")
        parts: list[str] = []
        final_in: int | None = None
        final_out: int | None = None
        async for chunk in provider.chat_stream(
                model, [ChatMessage(role="user", content=prompt)],
                ChatOptions(keep_alive=keep_alive), cancel):
            if cancel.is_set():
                break
            if chunk.content:
                parts.append(chunk.content)
                await emit({"type": "member_delta", "index": idx, "content": chunk.content})
            if chunk.done:
                final_in = chunk.input_tokens
                final_out = chunk.output_tokens
        method = "exact" if final_in is not None else "estimated"
        await self.usage.record(
            conversation_id=None, message_id=None, model=model, provider=provider.name,
            input_tokens=final_in or 0, output_tokens=final_out or 0, method=method,
            cost_eur=0.0, team_id=member["team_id"], team_member_id=member["id"])
        await self.teams.add_member_tokens(member["id"], final_in or 0, final_out or 0)
        text = "".join(parts).strip()
        await emit({"type": "member_done", "index": idx, "role": member["role"],
                    "status": "stopped" if cancel.is_set() else "complete",
                    "input_tokens": final_in or 0, "output_tokens": final_out or 0,
                    "result": text[:2000]})
        return text, final_in or 0, final_out or 0
