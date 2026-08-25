"""ToolExecutor — the gateway every model tool call passes through.

Order of operations (never re-order):
  1. permission policy capability check (fail fast, audited as denied)
  2. argument validation
  3. human approval for write/exec tiers (when an approver is attached)
  4. handler execution inside the sandbox
  5. audit log row in ``executions`` — ALWAYS, including denials/errors
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Awaitable, Callable

from ..core.errors import PathEscapeError
from ..db.repo import ExecutionsRepo
from ..security.permissions import PermissionPolicy
from .builtin import check_command_allowed, preview_diff
from .registry import ToolContext, ToolRegistry, ToolResult, ToolSpec

log = logging.getLogger("aicc.tools")

# approver: (spec, args, preview) → True (approved) / False (denied)
Approver = Callable[[ToolSpec, dict[str, Any], str | None], Awaitable[bool]]


class ToolExecutor:
    def __init__(self, registry: ToolRegistry, executions: ExecutionsRepo):
        self.registry = registry
        self.executions = executions

    async def execute(self, name: str, args: dict[str, Any], *, ctx: ToolContext,
                      policy: PermissionPolicy,
                      approver: Approver | None = None) -> ToolResult:
        spec = self.registry.get(name)
        t0 = time.monotonic()
        if spec is None:
            return ToolResult(ok=False, output="", error=f"unknown tool '{name}'")

        # 1+2. static gates
        error: str | None = None
        try:
            policy.require(spec.capability)
        except Exception as exc:
            error = getattr(exc, "message", str(exc))
        if error is None:
            error = self.registry.validate_args(spec, args)
        if error is None and name == "shell_run":
            error = check_command_allowed(args.get("command", ""), policy)
        if error is not None:
            await self._audit(spec_name=name, args=args, status="denied",
                              ctx=ctx, error=error, t0=t0)
            return ToolResult(ok=False, output="", error=error)

        # 3. human approval for mutating tiers
        if spec.requires_approval:
            preview = preview_diff(name, args, ctx.root)
            approved = await approver(spec, args, preview) if approver else False
            if not approved:
                await self._audit(spec_name=name, args=args, status="denied_by_user",
                                  ctx=ctx, error="user denied approval", t0=t0)
                return ToolResult(ok=False, output="",
                                  error="This action was denied by the user.")

        # 3b. snapshot originals before a mutating write (P12 undo)
        if ctx.snapshot is not None and name in {"fs_write", "fs_edit"}:
            try:
                ctx.snapshot.record(str(args.get("path") or ""), ctx.root)
            except Exception:
                log.warning("snapshot record failed for %s", name, exc_info=True)

        # 4. execute
        try:
            result = await spec.handler(args, ctx)
        except PathEscapeError as exc:
            # sandbox violation → clean policy denial, never a "crash"
            result = ToolResult(ok=False, output="",
                                error=f"path outside the workspace blocked: "
                                      f"{getattr(exc, 'message', str(exc))}")
            await self._audit(spec_name=name, args=args, status="denied",
                              ctx=ctx, error=result.error, t0=t0, result=result)
            return result
        except Exception as exc:
            log.exception("tool %s crashed", name)
            result = ToolResult(ok=False, output="", error=f"internal tool error: {exc}")

        # 5. audit — always
        await self._audit(spec_name=name, args=args,
                          status="ok" if result.ok else "error", ctx=ctx,
                          error=result.error, t0=t0, result=result)
        return result

    async def _audit(self, *, spec_name: str, args: dict, status: str,
                     ctx: ToolContext, error: str | None, t0: float,
                     result: ToolResult | None = None) -> None:
        try:
            ms = round((time.monotonic() - t0) * 1000, 1)
            log_text = ""
            if result is not None and result.diff:
                log_text = result.diff[:4000]
            if result is not None and result.output:
                log_text = (log_text + "\n--- output ---\n" if log_text else "") \
                    + result.output[:4000]
            if error:
                log_text = (log_text + "\n" if log_text else "") + f"error: {error}"
            await self.executions.log(
                kind=f"tool:{spec_name}", status=status,
                command=json.dumps(args, default=str)[:2000],
                actor=ctx.actor, exit_code=result.exit_code if result else None,
                log_text=f"[run {ctx.run_id or '-'}] [{ms} ms] {log_text}")
        except Exception:  # auditing must never crash a run
            log.exception("audit logging failed for %s", spec_name)
