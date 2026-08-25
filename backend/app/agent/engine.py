"""Agent engine (P3) — the real model→tool loop.

Flow per run (all events stream to the SSE consumer via an internal queue):
  resolve model → CostGuard (every step, pre-network) → model streams text
  and/or tool_calls → tool calls pass the gateway (permission policy →
  argument validation → human approval for write/exec → sandboxed
  execution → audit row) → results feed back → next step …
until the model stops calling tools (final answer), a guard trips
(max steps, repeated failures, user denial, cancellation).

Safety invariants:
* no direct provider access except through ModelRouter (no silent switches);
* the CostGuard runs BEFORE EVERY provider call in the loop;
* the model never sees tool results from outside the sandbox executor;
* approvals block the loop server-side until the user decides (or timeout).
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any, AsyncIterator

from ..core.errors import BadRequest
from ..db.repo import AgentRunsRepo, ApprovalsRepo, UsageRepo
from ..observability.metrics import metrics
from ..providers.base import ChatMessage, ChatOptions
from ..providers.registry import ProviderRegistry
from ..memory.service import MemoryService
from ..security.permissions import Capability, PermissionPolicy
from ..services.context import compact_messages, effective_num_ctx, summarize_middle
from ..services.cost_guard import CostGuard
from ..services.model_router import ModelRouter
from ..services.project_service import ProjectService
from ..services.settings_service import SettingsService
from ..tools.executor import ToolExecutor
from ..tools.registry import ToolContext, ToolRegistry, ToolSpec
from .prompts import build_agent_system_prompt

log = logging.getLogger("aicc.agent")

MAX_STEPS = 16
APPROVAL_TIMEOUT_S = 600
MAX_CONSECUTIVE_TOOL_ERRORS = 3

_CAPABILITY_SETTINGS = {
    Capability.FILESYSTEM_READ: "cap_filesystem_read",
    Capability.FILESYSTEM_WRITE: "cap_filesystem_write",
    Capability.COMMAND_EXECUTE: "cap_command_execute",
    Capability.NETWORK_FETCH: "cap_network_fetch",
    Capability.GIT_OPERATE: "cap_git_operate",
    Capability.MEMORY: "cap_memory",
}


class RunManager:
    """Tracks in-flight agent runs for cooperative cancellation."""

    def __init__(self):
        self._cancels: dict[str, asyncio.Event] = {}

    def start(self, run_id: str) -> asyncio.Event:
        event = asyncio.Event()
        self._cancels[run_id] = event
        return event

    def finish(self, run_id: str) -> None:
        self._cancels.pop(run_id, None)

    def stop(self, run_id: str) -> bool:
        event = self._cancels.get(run_id)
        if event is None:
            return False
        event.set()
        return True


class AgentEngine:
    def __init__(self, *, runs: AgentRunsRepo, approvals: ApprovalsRepo,
                 usage: UsageRepo, registry: ProviderRegistry,
                 router: ModelRouter, guard: CostGuard,
                 settings: SettingsService, tools: ToolRegistry,
                 executor: ToolExecutor, runs_manager: RunManager,
                 workspace_root: Path,
                 projects: ProjectService | None = None,
                 memory: MemoryService | None = None):
        self.runs = runs
        self.approvals = approvals
        self.usage = usage
        self.registry = registry
        self.router = router
        self.guard = guard
        self.settings = settings
        self.tools = tools
        self.executor = executor
        self.runs_manager = runs_manager
        self.workspace_root = workspace_root
        self.projects = projects
        self.memory = memory
        self._pending_approvals: dict[str, asyncio.Future[bool]] = {}

    # ── policy ───────────────────────────────────────────────────────
    async def build_policy(self) -> PermissionPolicy:
        granted: set[Capability] = set()
        for cap, key in _CAPABILITY_SETTINGS.items():
            if await self.settings.get_typed(key):
                granted.add(cap)
        return PermissionPolicy(granted=granted)

    async def capabilities_state(self) -> dict[str, bool]:
        return {cap.value: await self.settings.get_typed(key)
                for cap, key in _CAPABILITY_SETTINGS.items()}

    # ── approvals ────────────────────────────────────────────────────
    async def decide_approval(self, approval_id: str, approved: bool) -> dict:
        await self.approvals.decide(approval_id, approved)
        future = self._pending_approvals.pop(approval_id, None)
        row = await self.approvals.get(approval_id)
        if future is not None and not future.done():
            future.set_result(approved)
        if row is None:
            raise BadRequest(f"Approval '{approval_id}' not found.",
                             code="APPROVAL_NOT_FOUND")
        return {"approval_id": approval_id,
                "status": "approved" if approved else "denied"}

    # ── run lifecycle (public generator) ─────────────────────────────
    async def stream_run(self, *, task: str, provider_name: str | None,
                         model_name: str | None,
                         skills_text: str | None = None,
                         project_id: int | None = None) -> AsyncIterator[dict[str, Any]]:
        if not task.strip():
            raise BadRequest("Agent task must not be empty.")

        project_row, project_root = None, None
        if project_id is not None:
            if self.projects is None:                      # pragma: no cover - wired in main
                raise BadRequest("Projects are not available in this build.")
            project_row, project_root = await self.projects.root_for_id(project_id)

        provider, model, model_row = await self.router.resolve(provider_name, model_name)
        run = await self.runs.create(task=task.strip(), provider=provider.name,
                                     model=model, skills=skills_text,
                                     project_id=project_id)
        run_id = run["id"]
        cancel = self.runs_manager.start(run_id)
        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        worker = asyncio.create_task(self._worker(
            queue=queue, run_id=run_id, task=task.strip(), provider=provider,
            model=model, model_row=model_row, skills_text=skills_text, cancel=cancel,
            project_row=project_row, project_root=project_root))
        try:
            while True:
                event = await queue.get()
                if event is None:
                    break
                yield event
        finally:
            if not worker.done():
                worker.cancel()
                try:
                    await worker   # let it persist the honest "stopped" finish
                except asyncio.CancelledError:
                    pass
            self.runs_manager.finish(run_id)

    # ── worker: the actual loop ──────────────────────────────────────
    async def _emit_steps_persist(self, queue, run_id, step, kind, content="", data=None):
        await self.runs.add_step(run_id, step, kind, content, data)

    async def _worker(self, *, queue: asyncio.Queue, run_id: str, task: str,
                      provider, model: str, model_row: dict | None,
                      skills_text: str | None,
                      cancel: asyncio.Event,
                      project_row: dict | None = None,
                      project_root: Path | None = None) -> None:
        t0 = time.monotonic()
        step = 0
        total_in = total_out = 0
        status = "complete"
        error_text: str | None = None
        final_answer_parts: list[str] = []

        async def emit(event: dict[str, Any]) -> None:
            await queue.put(event)

        async def approver(spec: ToolSpec, args: dict, preview: str | None) -> bool:
            row = await self.approvals.create(run_id, spec.name, args,
                                              spec.danger.value, preview)
            await emit({"type": "approval_required",
                        "approval_id": row["id"], "tool": spec.name,
                        "args": args, "preview": preview,
                        "danger": spec.danger.value,
                        "timeout_s": APPROVAL_TIMEOUT_S})
            await self._emit_steps_persist(queue, run_id, step, "approval",
                                           f"{spec.name} approval requested",
                                           {"approval_id": row["id"], "args": args})
            loop = asyncio.get_running_loop()
            future: asyncio.Future[bool] = loop.create_future()
            self._pending_approvals[row["id"]] = future
            try:
                return await asyncio.wait_for(future, timeout=APPROVAL_TIMEOUT_S)
            except asyncio.TimeoutError:
                await self.approvals.expire(row["id"])
                await emit({"type": "approval_decided", "approval_id": row["id"],
                            "status": "expired"})
                return False
            finally:
                self._pending_approvals.pop(row["id"], None)

        try:
            policy = await self.build_policy()
            num_ctx = effective_num_ctx(await self.settings.get_typed("num_ctx"),
                                        (model_row or {}).get("context_length"))
            keep_alive = await self.settings.get_typed("keep_alive")
            custom_instructions = await self.settings.get_typed("custom_instructions")
            model_caps = set((model_row or {}).get("capabilities_list")
                             or json.loads((model_row or {}).get("capabilities_json") or "[]"))
            await emit({"type": "meta", "run_id": run_id, "model": model,
                        "provider": provider.name,
                        "capabilities": {c.value: c in policy.granted for c in Capability},
                        "max_steps": MAX_STEPS,
                        "project": {"id": project_row["id"], "name": project_row["name"]}
                        if project_row else None})
            tools_wanted = self.tools.names()
            if "tools" not in model_caps and provider.is_local:
                await emit({"type": "note", "level": "warn",
                            "message": f"Model '{model}' does not advertise tool-calling "
                                       "support — the run may fail. Models like qwen3:8b "
                                       "or llama3.1:8b are recommended for agents."})
            tool_schemas = self.tools.schemas(tools_wanted)
            effective_root = project_root or self.workspace_root
            # P8: standing instructions (AGENT.md) + persistent memory join
            # the per-request skills — everything labeled in the prompt.
            agent_md = None
            memory_block = None
            if self.memory is not None:
                agent_md = await self.memory.build_skills_text(
                    self.workspace_root, project_root)
                memory_block = await self.memory.memory_text()
            combined_skills = "\n\n".join(
                p for p in (agent_md, skills_text) if p) or None
            system_prompt = build_agent_system_prompt(
                workspace_root=str(effective_root),
                tools=self.tools.describe_all(), skills_text=combined_skills,
                memory_text=memory_block,
                custom_instructions=custom_instructions or None,
                project_name=project_row["name"] if project_row else None)
            await self.runs.add_step(run_id, 0, "note", "run started",
                                     {"task": task, "model": model})

            history: list[ChatMessage] = [
                ChatMessage(role="system", content=system_prompt),
                ChatMessage(role="user", content=task),
            ]
            ctx = ToolContext(workspace_root=self.workspace_root,
                              project_root=project_root, run_id=run_id,
                              memory=self.memory)
            consecutive_errors = 0

            hit_max_steps = False
            while True:
                if cancel.is_set():
                    status = "stopped"
                    break
                if step >= MAX_STEPS:        # model still wanted tools → give up honestly
                    hit_max_steps = True
                    break
                step += 1
                # per-step CostGuard — BEFORE any provider traffic
                totals = await self.usage.totals()
                await self.guard.guard_request(provider, model, model_row,
                                               total_spent_eur=totals["cost_eur"])
                # context management (P2): compact before overflow
                history, compacted = await compact_messages(
                    history, num_ctx,
                    summarize=lambda omitted: summarize_middle(provider, model, omitted,
                                                               keep_alive))
                if compacted:
                    await emit({"type": "note", "level": "info",
                                "message": "Context compacted (older turns summarized to "
                                           "fit the context window)."})

                await emit({"type": "step", "step": step})
                content_parts: list[str] = []
                tool_calls: list[dict[str, Any]] = []
                final_in: int | None = None
                final_out: int | None = None

                async for chunk in provider.chat_stream(
                        model, history,
                        ChatOptions(num_ctx=num_ctx, keep_alive=keep_alive,
                                    tools=tool_schemas), cancel):
                    if cancel.is_set():
                        break
                    if chunk.content:
                        content_parts.append(chunk.content)
                        await emit({"type": "delta", "step": step, "content": chunk.content})
                    if chunk.tool_calls:
                        tool_calls.extend(chunk.tool_calls)
                    if chunk.done:
                        final_in = chunk.input_tokens
                        final_out = chunk.output_tokens

                if cancel.is_set():          # stop landed mid-stream → honor it now
                    status = "stopped"
                    break

                step_in = final_in or 0
                step_out = final_out or 0
                total_in += step_in
                total_out += step_out
                await self.usage.record(
                    conversation_id=None, message_id=None, model=model,
                    provider=provider.name, input_tokens=step_in, output_tokens=step_out,
                    method="exact" if final_in is not None else "estimated", cost_eur=0.0)
                await self.runs.add_tokens(run_id, step_in, step_out)

                step_text = "".join(content_parts).strip()
                if step_text:
                    final_answer_parts.append(step_text)
                await self._emit_steps_persist(
                    queue, run_id, step, "model", step_text,
                    {"input_tokens": final_in, "output_tokens": final_out,
                     "tool_calls": bool(tool_calls)})

                # ── final answer? ──
                if not tool_calls:
                    break

                # ── execute tool calls through the gateway ──
                history.append(ChatMessage(role="assistant",
                                           content="".join(content_parts),
                                           tool_calls=tool_calls))
                denied = False
                for call in tool_calls:
                    if cancel.is_set():
                        break
                    fn = call.get("function") or {}
                    name = fn.get("name") or ""
                    args = fn.get("arguments") or {}
                    if not isinstance(args, dict):
                        args = {"_raw": args}
                    call_id = call.get("id") or uuid.uuid4().hex[:8]

                    await emit({"type": "tool_call", "step": step, "call_id": call_id,
                                "tool": name, "args": args})
                    await self._emit_steps_persist(queue, run_id, step, "tool_call",
                                                   name, {"call_id": call_id, "args": args})

                    result = await self.executor.execute(
                        name, args, ctx=ctx, policy=policy, approver=approver)
                    spec = self.tools.get(name)
                    danger = spec.danger.value if spec else "read"

                    if result.error == "This action was denied by the user.":
                        status = "denied"
                        denied = True
                        error_text = "Run stopped: the user denied an action."
                        history.append(ChatMessage(
                            role="tool", name=name, tool_call_id=call_id,
                            content="DENIED BY USER — the action was refused."))
                        await emit({"type": "approval_decided",
                                    "approval_id": None, "status": "denied",
                                    "tool": name})
                        break
                    if result.error and "not granted" in (result.error or ""):
                        status = "error"
                        error_text = (f"Run stopped: capability for tool '{name}' is "
                                      f"not granted by policy. {result.error}")
                        await emit({"type": "tool_result", "step": step,
                                    "call_id": call_id, "tool": name, "ok": False,
                                    "danger": danger, "error": result.error})
                        denied = True     # fail fast on policy denial
                        break

                    consecutive_errors = 0 if result.ok else consecutive_errors + 1
                    result_text = result.as_text()
                    await emit({"type": "tool_result", "step": step, "call_id": call_id,
                                "tool": name, "ok": result.ok, "danger": danger,
                                "exit_code": result.exit_code, "ms": result.ms,
                                "output": result_text[:6000],
                                "diff": (result.diff or "")[:6000] or None})
                    await self._emit_steps_persist(
                        queue, run_id, step, "tool_result",
                        result_text[:4000],
                        {"tool": name, "ok": result.ok, "call_id": call_id,
                         "ms": result.ms})
                    history.append(ChatMessage(role="tool", name=name,
                                               tool_call_id=call_id, content=result_text))

                    if consecutive_errors >= MAX_CONSECUTIVE_TOOL_ERRORS:
                        status = "error"
                        error_text = ("Run stopped: circuit breaker — "
                                      f"{MAX_CONSECUTIVE_TOOL_ERRORS} consecutive tool "
                                      "failures.")
                        denied = True
                        break

                if denied:
                    break

            if hit_max_steps:
                # the model still wanted tool calls when the budget ran out —
                # say so honestly instead of pretending the task finished.
                await emit({"type": "note", "level": "warn",
                            "message": f"Reached the safety limit of {MAX_STEPS} "
                                       "steps — stopping here. Narrow the task or "
                                       "raise the limit for longer runs."})
                await self.runs.add_step(run_id, step, "note",
                                         f"max steps ({MAX_STEPS}) reached")

        except asyncio.CancelledError:
            # task cancelled (client disconnect / team stop) — still write
            # honest bookeeping before going down; do not re-raise.
            status = "stopped"
        except Exception as exc:  # provider/guard errors land here
            status = "error"
            error_text = getattr(exc, "message", str(exc))
            metrics.chat_errors += 1
            log.warning("agent run %s failed: %s", run_id, error_text)
        finally:
            for approval_id, future in list(self._pending_approvals.items()):
                if not future.done():
                    future.set_result(False)
                    await self.approvals.expire(approval_id)
            self._pending_approvals.clear()

        elapsed = round(time.monotonic() - t0, 1)
        result_text = "\n\n".join(final_answer_parts).strip()
        await self.runs.finish(run_id, status=status, result=result_text,
                               error=error_text, steps=step)
        await self.runs.add_step(run_id, step, "note",
                                 f"run finished: {status}"
                                 + (f" — {error_text}" if error_text else ""))
        await emit({"type": "usage", "input_tokens": total_in,
                    "output_tokens": total_out, "total_tokens": total_in + total_out,
                    "steps": step, "elapsed_s": elapsed})
        if status == "error" and error_text:
            await emit({"type": "error", "code": "AGENT_ERROR", "message": error_text})
        await emit({"type": "done", "run_id": run_id, "status": status,
                    "result": result_text, "error": error_text, "steps": step,
                    "elapsed_s": elapsed})
        await queue.put(None)
