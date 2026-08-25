"""Compare Mode (P4) — one prompt, N models, side-by-side.

Every comparison goes through the exact production path: ModelRouter
resolution (no silent provider switches), CostGuard before ANY provider
traffic (a paid/blocked model fails its own slot honestly while the rest
keep streaming), and token accounting lands in the same usage ledger as
chat and agent runs.

VRAM-aware scheduling: calls against the same LOCAL provider run
sequentially (one model resident at a time); cloud providers get up to
``CLOUD_CONCURRENCY`` parallel slots. This is documented in the emitted
``queued`` statuses — nothing pretends to be parallel that isn't.
"""
from __future__ import annotations

import asyncio
import contextlib
import time
from typing import Any, AsyncIterator

from ..core.errors import AppError, BadRequest
from ..db.repo import UsageRepo
from ..providers.base import ChatMessage, ChatOptions
from ..services.cost_guard import CostGuard
from ..services.context import effective_num_ctx
from ..services.model_router import ModelRouter
from ..services.settings_service import SettingsService

MAX_COMPARISONS = 4
CLOUD_CONCURRENCY = 4

_compare_seq = 0


def _parse_spec(spec: str) -> tuple[str | None, str]:
    """"provider/model" (explicit) or bare "model" (catalog-resolved)."""
    spec = spec.strip()
    if "/" in spec:
        provider, model = spec.split("/", 1)
        return provider.strip() or None, model.strip()
    return None, spec


class CompareService:
    def __init__(self, *, router: ModelRouter, guard: CostGuard,
                 usage: UsageRepo, settings: SettingsService):
        self.router = router
        self.guard = guard
        self.usage = usage
        self.settings = settings

    async def stream(self, prompt: str, specs: list[str],
                     cancel: asyncio.Event) -> AsyncIterator[dict[str, Any]]:
        prompt = prompt.strip()
        if not prompt:
            raise BadRequest("Compare prompt must not be empty.")
        if not 2 <= len(specs) <= MAX_COMPARISONS:
            raise BadRequest(f"Compare needs 2–{MAX_COMPARISONS} models.",
                             code="COMPARE_MODEL_COUNT")
        if len(set(specs)) != len(specs):
            raise BadRequest("Duplicate models in comparison — pick distinct ones.",
                             code="COMPARE_DUPLICATE")

        # resolve all slots up-front; a bad spec errors the whole request
        slots: list[dict[str, Any]] = []
        for i, spec in enumerate(specs):
            provider_name, model_name = _parse_spec(spec)
            provider, model, row = await self.router.resolve(provider_name, model_name)
            slots.append({"index": i, "provider": provider, "model": model, "row": row})

        num_ctx_setting = await self.settings.get_typed("num_ctx")
        keep_alive = await self.settings.get_typed("keep_alive")
        totals = await self.usage.totals()

        yield {"type": "meta", "comparisons": [
            {"index": s["index"], "provider": s["provider"].name, "model": s["model"],
             "is_local": s["provider"].is_local} for s in slots]}

        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        # one serialization point per LOCAL provider; clouds run wide
        local_gates: dict[str, asyncio.Semaphore] = {}
        for s in slots:
            if s["provider"].is_local and s["provider"].name not in local_gates:
                local_gates[s["provider"].name] = asyncio.Semaphore(1)
        cloud_gate = asyncio.Semaphore(CLOUD_CONCURRENCY)
        # deterministic "queued" status: the 2nd+ slot on a shared local gate
        # genuinely waits (VRAM safety) — say so up front
        seen_local: set[str] = set()
        for s in slots:
            if s["provider"].is_local and s["provider"].name in local_gates:
                if s["provider"].name in seen_local:
                    s["queued_first"] = True
                seen_local.add(s["provider"].name)

        async def worker(slot: dict) -> None:
            i = slot["index"]
            provider = slot["provider"]
            model = slot["model"]
            gate = local_gates.get(provider.name) or cloud_gate
            if slot.get("queued_first"):
                await queue.put({"type": "slot_status", "index": i, "status": "queued"})
            async with gate:
                if cancel.is_set():
                    await queue.put({"type": "slot_status", "index": i, "status": "cancelled"})
                    return
                await queue.put({"type": "slot_status", "index": i, "status": "running"})
                t0 = time.monotonic()
                try:
                    await self.guard.guard_request(provider, model, slot["row"],
                                                   total_spent_eur=totals["cost_eur"])
                    num_ctx = effective_num_ctx(num_ctx_setting,
                                                (slot["row"] or {}).get("context_length"))
                    messages = [ChatMessage(role="user", content=prompt)]
                    final_in: int | None = None
                    final_out: int | None = None
                    tps: float | None = None
                    async for chunk in provider.chat_stream(
                            model, messages,
                            ChatOptions(num_ctx=num_ctx, keep_alive=keep_alive), cancel):
                        if cancel.is_set():
                            break
                        if chunk.content:
                            await queue.put({"type": "delta", "index": i,
                                             "content": chunk.content})
                        if chunk.done:
                            final_in = chunk.input_tokens
                            final_out = chunk.output_tokens
                            tps = chunk.output_tps
                    method = "exact" if final_in is not None else "estimated"
                    await self.usage.record(
                        conversation_id=None, message_id=None, model=model,
                        provider=provider.name, input_tokens=final_in or 0,
                        output_tokens=final_out or 0, method=method, cost_eur=0.0)
                    await queue.put({
                        "type": "model_done", "index": i,
                        "status": "stopped" if cancel.is_set() else "complete",
                        "input_tokens": final_in, "output_tokens": final_out,
                        "token_method": method,
                        "tokens_per_second": round(tps, 1) if tps else None,
                        "elapsed_s": round(time.monotonic() - t0, 1)})
                except AppError as exc:
                    await queue.put({"type": "model_done", "index": i, "status": "error",
                                     "code": exc.code, "message": exc.message,
                                     "elapsed_s": round(time.monotonic() - t0, 1)})
                except Exception as exc:  # provider network failure etc.
                    await queue.put({"type": "model_done", "index": i, "status": "error",
                                     "code": "PROVIDER_ERROR", "message": str(exc)[:500],
                                     "elapsed_s": round(time.monotonic() - t0, 1)})

        # every slot ends with exactly one model_done → that's the termination signal
        async def guard_worker(slot: dict) -> None:
            try:
                await worker(slot)
            except Exception:  # pragma: no cover - worker never raises, belt & braces
                await queue.put({"type": "model_done", "index": slot["index"],
                                 "status": "error", "code": "INTERNAL_ERROR",
                                 "message": "compare worker crashed", "elapsed_s": 0.0})

        workers = [asyncio.create_task(guard_worker(s)) for s in slots]
        done_count = 0
        try:
            while done_count < len(workers):
                event = await queue.get()
                yield event
                if event.get("type") == "model_done":
                    done_count += 1
        finally:
            cancel.set()
            for w in workers:
                if not w.done():
                    w.cancel()
                    with contextlib.suppress(BaseException):
                        await w
        yield {"type": "done"}
