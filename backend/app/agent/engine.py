"""Controlled Agent engine.

Workflow: PLAN → EXECUTE → VERIFY → FIX → FINALIZE.

The agent can only act through the sandboxed :class:`FileToolbox` and the
allowlisted :class:`CommandRunner`; every action and every model call is
persisted (agent_runs / agent_steps / usage_events / executions). The
model communicates with a strict, parseable one-action-at-a-time protocol
so its output is validated and executed by code — never directly.

Never exposes chain-of-thought: events carry decisions, actions, tool
calls, results, errors and status only.
"""
from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from typing import Any, AsyncIterator

from ..core.errors import BadRequest
from ..db.repo import AgentRepo, UsageRepo
from ..providers.base import ChatMessage
from ..services.model_runner import ModelRunner, UsageSink
from ..services.settings_service import SettingsService
from ..tools.files import FileToolbox
from ..tools.runner import CommandRunner

log = logging.getLogger("aicc.agent")

MAX_HISTORY_TURNS = 14
MAX_RESULT_IN_HISTORY = 1200
MAX_ACTIONS_PER_TURN = 3

SYSTEM_PROMPT = """You are an autonomous software engineer working inside a secure sandbox.
You have these tools — use ONLY these tools, one per line:

TOOL list_files [path="."]
TOOL read_file path="relative/path"
TOOL write_file path="relative/path"  (followed by a block)
<<<FILE
content (exact bytes)
FILE
TOOL edit_file path="relative/path"  (followed by two blocks)
<<<OLD
exact old text
OLD
<<<NEW
exact new text
NEW
TOOL search_files pattern="text-to-find" [path="."]
TOOL create_directory path="relative/path"
TOOL delete_file path="relative/path"
TOOL run cmd="command string"        (allowlisted, no shell; e.g. 'pytest -q')
TOOL note <short finding or decision>
TOOL done <final summary of what you delivered>

Rules:
- Paths are always RELATIVE to the workspace root. Absolute paths and '..' are blocked.
- One action per message. Do not narrate the action in prose; the tool line is the message.
- After a tool result you must choose the next action.
- Work in small steps: inspect, then implement, then verify with tests/typecheck/lint.
- When the task is fully complete, reply with exactly: TOOL done <summary>"""


# ── parsing ──────────────────────────────────────────────────────────
_ARG_RE = re.compile(r'(\w+)=("([^"]*)"|(\S+))')


def parse_tool_lines(text: str) -> list[dict[str, Any]]:
    """Parse the strict TOOL protocol. Returns up to MAX_ACTIONS_PER_TURN actions."""
    lines = text.splitlines()
    actions: list[dict[str, Any]] = []
    i = 0
    while i < len(lines) and len(actions) < MAX_ACTIONS_PER_TURN:
        line = lines[i].strip()
        if not line.startswith("TOOL "):
            i += 1
            continue
        rest = line[5:].strip()
        name, _, tail = rest.partition(" ")
        name = name.strip()
        action: dict[str, Any] = {"tool": name}
        if tail:
            if '="' in tail:
                for m in _ARG_RE.finditer(tail):
                    action[m.group(1)] = m.group(3) if m.group(3) is not None else m.group(4)
            else:
                action["text"] = tail.strip()
        # collect content blocks (used by write_file / edit_file)
        j = i + 1
        saw_next_tool = False
        while j < len(lines):
            marker = lines[j].strip()
            if marker == "<<<FILE":
                content = _read_block(lines, j + 1, "FILE")
                action["content"] = content[0]
                j = content[1]
                break
            if marker == "<<<OLD":
                old = _read_block(lines, j + 1, "OLD")
                action["old"] = old[0]
                j = old[1]
                continue
            if marker == "<<<NEW":
                new = _read_block(lines, j + 1, "NEW")
                action["new"] = new[0]
                j = new[1]
                continue
            if marker.startswith("<<<"):
                break
            if marker.startswith("TOOL "):
                saw_next_tool = True
                break
            j += 1
        actions.append(action)
        i = j if saw_next_tool else j + 1
    return actions


def _read_block(lines: list[str], start: int, tag: str) -> tuple[str, int]:
    content: list[str] = []
    i = start
    while i < len(lines):
        if lines[i].strip() == tag:
            return "\n".join(content), i
        content.append(lines[i])
        i += 1
    return "\n".join(content), i


def format_observation(result: dict[str, Any]) -> str:
    """Compact, honest tool result shown to the model."""
    ok = result.get("ok", False)
    if not ok:
        return f"RESULT ERROR: {result.get('error', 'unknown error')}"
    if "tree" in result:
        return f"RESULT OK — files:\n{result['tree']}"
    if "matches" in result:
        return f"RESULT OK — {result['count']} match(es):\n" + "\n".join(
            f"- {m['path']}" for m in result["matches"][:20])
    if "content" in result:
        return f"RESULT OK — content:\n{result['content']}"
    return "RESULT OK — " + ", ".join(
        f"{k}={v}" for k, v in result.items() if k not in ("ok",))


# ── engine ───────────────────────────────────────────────────────────
class AgentEngine:
    def __init__(self, *, runner: ModelRunner, settings: SettingsService,
                 agents: AgentRepo, usage: UsageRepo,
                 workspace_root: Path,
                 executions_repo,  # ExecutionsRepo
                 ):
        self.runner = runner
        self.settings = settings
        self.agents = agents
        self.usage = usage
        self.workspace_root = Path(workspace_root).resolve()
        self.executions = executions_repo

    def workspace_for(self, project_id: int | None) -> Path:
        """Per-project sandbox: <root>/projects/<default|p<id>>.

        The same layout is used by GitService so Agent and Git always work
        on the same directory for a given project.
        """
        name = "default" if project_id is None else f"p{project_id}"
        path = self.workspace_root / "projects" / name
        path.mkdir(parents=True, exist_ok=True)
        return path

    async def _settings(self) -> dict[str, Any]:
        rt = await self.settings.as_dict()
        return {
            "num_ctx": rt["num_ctx"],
            "keep_alive": rt["keep_alive"],
            "max_steps": int(rt.get("agent_max_steps", 20)),
            "max_fix_rounds": int(rt.get("agent_max_fix_rounds", 2)),
        }

    async def run(self, *, task: str, project_id: int | None = None,
                  provider_name: str | None = None, model_name: str | None = None,
                  cancel: asyncio.Event | None = None) -> AsyncIterator[dict[str, Any]]:
        cancel = cancel or asyncio.Event()
        cfg = await self._settings()
        ws = self.workspace_for(project_id)
        run = await self.agents.create(task, project_id, str(ws))
        run_id = run["id"]
        sink = UsageSink(conversation_id=None, team_id=None, team_member_id=None)
        tools = FileToolbox(ws, self.executions, actor=f"agent:{run_id}")
        runner_cmd = CommandRunner(ws, self.executions,
                                   actor=f"agent:{run_id}",
                                   timeout=float(await self.settings.get_typed("agent_cmd_timeout"))
                                   if await self.settings.get_typed("agent_cmd_timeout")
                                   else 120.0)

        yield {"type": "run", "run_id": run_id, "workspace": str(ws)}
        try:
            # ── PLAN ──
            await self.agents.update(run_id, status="running", stage="plan")
            yield {"type": "stage", "run_id": run_id, "stage": "plan", "status": "running"}
            plan = await self._make_plan(task, provider_name, model_name)
            await self.agents.update(run_id, plan=plan)
            yield {"type": "activity", "run_id": run_id, "stage": "plan",
                   "kind": "plan", "content": plan}

            # ── EXECUTE ──
            await self.agents.update(run_id, stage="execute")
            yield {"type": "stage", "run_id": run_id, "stage": "execute", "status": "running"}
            history: list[dict[str, str]] = []
            step = 0
            deliverable = ""
            done = False

            while step < cfg["max_steps"] and not cancel.is_set():
                step += 1
                gen = await self._agent_turn(task, plan, history, provider_name,
                                             model_name, sink)
                if gen.status == "error":
                    await self._log_step(run_id, step, "execute", "model", None,
                                         "model call failed", "error", gen.error or "")
                    yield {"type": "error", "run_id": run_id, "code": "MODEL_ERROR",
                           "message": gen.error}
                    break
                actions = parse_tool_lines(gen.text)
                if not actions:
                    # no tool line — gently nudge, count as one attempt
                    history.append({"role": "assistant", "content": "(no action)"[:200]})
                    history.append({"role": "user",
                                    "content": "RESULT ERROR: no valid action found. "
                                               "Output exactly one TOOL line."})
                    continue

                for action in actions:
                    if cancel.is_set():
                        break
                    tool = action.get("tool", "")
                    label = tool + (f" {action.get('path') or action.get('cmd') or ''}"
                                    if action.get("path") or action.get("cmd") else "")
                    if tool == "done":
                        deliverable = action.get("text") or action.get("content") or ""
                        done = True
                        break
                    result = await self._execute_action(tools, runner_cmd, action)
                    await self._log_step(run_id, step, "execute", tool,
                                         action.get("path") or action.get("cmd"),
                                         label, "done" if result.get("ok") else "error",
                                         format_observation(result)[:2000])
                    yield {"type": "activity", "run_id": run_id, "stage": "execute",
                           "kind": "tool_call", "tool": tool, "content": label,
                           "ok": bool(result.get("ok"))}
                    yield {"type": "tool_result", "run_id": run_id, "tool": tool,
                           "content": format_observation(result)[:3000],
                           "ok": bool(result.get("ok"))}
                    history.append({"role": "assistant", "content": gen.text[:600]})
                    history.append({"role": "user",
                                    "content": format_observation(result)[:MAX_RESULT_IN_HISTORY]})
                if done:
                    break
                if not history:
                    history.append({"role": "assistant", "content": gen.text[:600]})
                    history.append({"role": "user", "content": "Continue."})
                history = history[-MAX_HISTORY_TURNS * 2:]
                yield {"type": "tokens", "run_id": run_id, "stage": "execute",
                       "input": sink.input_total, "output": sink.output_total,
                       "cost": sink.cost_total}

            if cancel.is_set():
                await self.agents.update(run_id, status="cancelled", stage="execute",
                                         summary="Cancelled by user.")
                yield {"type": "done", "run_id": run_id, "status": "cancelled",
                       "summary": "Cancelled by user."}
                return

            if not done:
                await self.agents.update(run_id, status="error", stage="execute",
                                         error="Step limit reached before completion.")
                yield {"type": "error", "run_id": run_id, "code": "STEP_LIMIT",
                       "message": f"Step limit ({cfg['max_steps']}) reached."}
                yield {"type": "done", "run_id": run_id, "status": "error",
                       "summary": "Step limit reached."}
                return

            # ── VERIFY ──
            await self.agents.update(run_id, stage="verify")
            yield {"type": "stage", "run_id": run_id, "stage": "verify", "status": "running"}
            issues = await self._verify(ws, runner_cmd, run_id, sink,
                                        provider_name, model_name)
            for check in issues["performed"]:
                yield {"type": "activity", "run_id": run_id, "stage": "verify",
                       "kind": "check", "content": check["label"],
                       "ok": check["ok"]}

            # ── FIX (if verification failed) ──
            if issues["failed"]:
                fix_round = 0
                while issues["failed"] and fix_round < cfg["max_fix_rounds"] \
                        and not cancel.is_set():
                    fix_round += 1
                    await self.agents.update(run_id, stage="fix")
                    yield {"type": "stage", "run_id": run_id, "stage": "fix",
                           "status": "running", "round": fix_round}
                    fail_text = "\n".join(
                        f"- {c['label']}:\n{c['output'][:800]}" for c in issues["failed"])
                    gen = await self._fix_turn(task, plan, fail_text, provider_name,
                                               model_name, sink)
                    if gen.status == "error":
                        yield {"type": "error", "run_id": run_id, "code": "MODEL_ERROR",
                               "message": gen.error}
                        break
                    actions = parse_tool_lines(gen.text)
                    if not actions:
                        yield {"type": "activity", "run_id": run_id, "stage": "fix",
                               "kind": "error", "content": "Model produced no action."}
                        break
                    for action in actions:
                        if action.get("tool") == "done":
                            break
                        result = await self._execute_action(tools, runner_cmd, action)
                        await self._log_step(run_id, step + fix_round, "fix",
                                             action.get("tool"),
                                             action.get("path") or action.get("cmd"),
                                             "fix", "done" if result.get("ok") else "error",
                                             format_observation(result)[:1200])
                        yield {"type": "activity", "run_id": run_id, "stage": "fix",
                               "kind": "tool_call", "tool": action.get("tool"),
                               "content": str(action.get("path") or action.get("cmd")),
                               "ok": bool(result.get("ok"))}
                    issues = await self._verify(ws, runner_cmd, run_id, sink,
                                                provider_name, model_name)
                    for check in issues["performed"]:
                        yield {"type": "activity", "run_id": run_id, "stage": "fix",
                               "kind": "check", "content": check["label"],
                               "ok": check["ok"]}

            # ── FINALIZE ──
            await self.agents.update(run_id, stage="finalize")
            yield {"type": "stage", "run_id": run_id, "stage": "finalize", "status": "running"}
            summary = await self._summary_turn(task, plan, deliverable,
                                               issues["failed"], provider_name,
                                               model_name, sink)
            status = "delivered" if not issues["failed"] else "delivered_with_issues"
            await self.agents.update(run_id, status=status, stage="finalize",
                                     summary=summary)
            await self.agents.add_tokens(run_id, sink.input_total, sink.output_total,
                                         sink.cost_total)
            yield {"type": "tokens", "run_id": run_id, "stage": "finalize",
                   "input": sink.input_total, "output": sink.output_total,
                   "cost": sink.cost_total}
            yield {"type": "done", "run_id": run_id, "status": status, "summary": summary}
        except Exception as exc:  # truly unexpected — never leave a hanging run
            log.exception("agent run %s failed", run_id)
            try:
                await self.agents.update(run_id, status="error",
                                         error=getattr(exc, "message", str(exc)))
            except Exception:  # pragma: no cover
                pass
            yield {"type": "error", "run_id": run_id, "code": "INTERNAL_ERROR",
                   "message": getattr(exc, "message", str(exc))}

    # ── internals ────────────────────────────────────────────────────
    async def _call(self, messages: list[ChatMessage], provider_name, model_name,
                    sink: UsageSink) -> Any:
        return await self.runner.generate(messages=messages,
                                          provider_name=provider_name,
                                          model_name=model_name, sink=sink)

    async def _make_plan(self, task: str, provider_name, model_name) -> str:
        prompt = ("Create a short, actionable implementation plan for the task. "
                  "List concrete steps (5–10), each on one line starting with '- '.\n\n"
                  f"TASK:\n{task}\n\n"
                  "You may list files you intend to create and checks you will run. "
                  "Answer with the plan only.")
        gen = await self._call(self.runner.messages(("system", SYSTEM_PROMPT),
                                                    ("user", prompt)),
                               provider_name, model_name, UsageSink())
        if gen.status == "error":
            raise BadRequest(f"Model failed to create a plan: {gen.error}",
                             code="MODEL_ERROR")
        text = gen.text.strip()
        return text[:4000] if text else "No plan produced."

    async def _agent_turn(self, task: str, plan: str, history: list[dict],
                          provider_name, model_name, sink: UsageSink) -> Any:
        messages: list[ChatMessage] = [ChatMessage(role="system", content=SYSTEM_PROMPT)]
        context = [f"TASK:\n{task}\n\nYOUR PLAN:\n{plan}\n\n"
                   "Work through the plan. Inspect before writing. Verify with tests "
                   "after implementing. Answer with exactly one TOOL action."]
        for h in history[-MAX_HISTORY_TURNS * 2:]:
            if h["role"] == "assistant" and not (h["content"].startswith("TOOL ")
                                                 or "(no action)" in h["content"]):
                continue
        messages.append(ChatMessage(role="user", content="\n".join(context)))
        for h in history[-MAX_HISTORY_TURNS * 2:]:
            messages.append(ChatMessage(role="assistant" if h["role"] == "assistant"
                                        else "user", content=h["content"][:4000]))
        return await self._call(messages, provider_name, model_name, sink)

    async def _fix_turn(self, task: str, plan: str, failures: str,
                        provider_name, model_name, sink: UsageSink) -> Any:
        prompt = (f"Verification of your work failed. Task: {task}\nPlan: {plan}\n"
                  f"Failures:\n{failures}\n\n"
                  "Fix the failures. Use read_file to inspect, then edit_file, then "
                  "run the failing check again. Answer with exactly one TOOL action.")
        return await self._call(self.runner.messages(("system", SYSTEM_PROMPT),
                                                     ("user", prompt)),
                                provider_name, model_name, sink)

    async def _summary_turn(self, task: str, plan: str, deliverable: str,
                            failures: list, provider_name, model_name,
                            sink: UsageSink) -> str:
        fail_note = f"\nNOTE: {len(failures)} verification check(s) still failing." if failures else ""
        prompt = (f"Write a short delivery summary (max 160 words, plain text) for the "
                  f"completed task. Include what was built, key files, and verification "
                  f"status.\n\nTASK: {task}\nPLAN: {plan}\nDELIVERABLE NOTE: "
                  f"{deliverable[:600]}{fail_note}")
        gen = await self._call(self.runner.messages(("system", SYSTEM_PROMPT),
                                                    ("user", prompt)),
                               provider_name, model_name, sink)
        return (gen.text or "").strip()[:3000]

    async def _execute_action(self, tools: FileToolbox, cmd_runner: CommandRunner,
                              action: dict[str, Any]) -> dict[str, Any]:
        tool = action.get("tool", "")
        try:
            if tool == "read_file":
                return await tools.read_file(action.get("path", ""))
            if tool == "write_file":
                return await tools.write_file(action.get("path", ""),
                                              action.get("content", ""))
            if tool == "edit_file":
                return await tools.edit_file(action.get("path", ""),
                                             action.get("old", ""),
                                             action.get("new", ""))
            if tool == "search_files":
                return await tools.search_files(action.get("pattern", ""),
                                                action.get("path", "."))
            if tool == "list_files":
                return await tools.list_files(action.get("path", "."))
            if tool == "create_directory":
                return await tools.create_directory(action.get("path", ""))
            if tool == "delete_file":
                return await tools.delete_file(action.get("path", ""))
            if tool == "run":
                return await cmd_runner.run(action.get("cmd", ""))
            if tool in ("note", "plan", "done"):
                return {"ok": True, "note": action.get("text", ""),
                        "content": action.get("text", "")}
            return {"ok": False, "error": f"unknown tool '{tool}'"}
        except BadRequest as exc:
            return {"ok": False, "error": exc.message}

    async def _verify(self, ws: Path, cmd_runner: CommandRunner, run_id: int,
                      sink: UsageSink, provider_name, model_name) -> dict:
        checks = CommandRunner.detect_checks(ws)
        performed: list[dict[str, Any]] = []
        for kind, spec in checks.items():
            result = await cmd_runner.run(spec["cmd"], timeout=300.0)
            ok = bool(result.get("ok"))
            performed.append({
                "kind": kind, "label": spec["desc"], "cmd": spec["cmd"], "ok": ok,
                "output": f"{result.get('stdout', '')}{result.get('stderr', '')}".strip(),
                "exit_code": result.get("exit_code"),
            })
            await self._log_step(run_id, 0, "verify", "run", spec["cmd"],
                                 spec["desc"], "done" if ok else "error",
                                 performed[-1]["output"][:1500])
        failed = [c for c in performed if not c["ok"]]
        return {"performed": performed, "failed": failed}

    async def _log_step(self, run_id: int, seq: int, stage: str, tool: str | None,
                        target: str | None, summary: str, status: str,
                        detail: str) -> None:
        try:
            await self.agents.add_step(run_id, seq, stage, tool, target,
                                       summary[:300], status, detail)
        except Exception:  # pragma: no cover
            log.exception("agent step log failed")
