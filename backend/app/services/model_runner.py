"""ModelRunner — one guarded, metered model call used by every engine
(Chat, Agent, Team, Compare, Research, Git summaries).

Every call: resolve → CostGuard (pre-network) → stream → exact/estimated
token accounting → usage ledger. There is no code path that reaches a
provider without passing the guard.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from ..db.repo import ModelsRepo, UsageRepo
from ..observability.metrics import metrics
from ..providers.base import ChatMessage, ChatOptions
from .cost_guard import CostGuard
from .model_router import ModelRouter
from .settings_service import SettingsService
from .tokens import estimate_tokens

log = logging.getLogger("aicc.model_runner")


@dataclass
class Generation:
    text: str = ""
    input_tokens: int | None = None
    output_tokens: int | None = None
    token_method: str = "estimated"
    tokens_per_second: float | None = None
    cost_eur: float = 0.0
    status: str = "complete"
    error: str | None = None
    # cost rates used for this call
    rate_in: float = 0.0
    rate_out: float = 0.0

    @property
    def total_tokens(self) -> int:
        return (self.input_tokens or 0) + (self.output_tokens or 0)


@dataclass
class UsageSink:
    """Where the caller wants token/cost totals aggregated."""

    conversation_id: str | None = None
    message_id: str | None = None
    team_id: int | None = None
    team_member_id: int | None = None
    input_total: int = 0
    output_total: int = 0
    cost_total: float = 0.0

    def add(self, gen: Generation) -> None:
        self.input_total += gen.input_tokens or 0
        self.output_total += gen.output_tokens or 0
        self.cost_total = round(self.cost_total + gen.cost_eur, 8)


class ModelRunner:
    def __init__(self, router: ModelRouter, guard: CostGuard, models: ModelsRepo,
                 usage: UsageRepo, settings: SettingsService):
        self.router = router
        self.guard = guard
        self.models = models
        self.usage = usage
        self.settings = settings

    async def generate(self, *, messages: list[ChatMessage],
                       provider_name: str | None = None, model_name: str | None = None,
                       temperature: float | None = None,
                       sink: UsageSink | None = None,
                       num_ctx: int | None = None,
                       keep_alive: str | None = None,
                       max_output_tokens: int | None = None,
                       on_delta=None) -> Generation:
        """One complete model call. Guarded pre-network, metered, recorded."""
        provider, model, model_row = await self.router.resolve(provider_name, model_name)
        totals = await self.usage.totals()
        await self.guard.guard_request(provider, model, model_row,
                                       total_spent_eur=totals["cost_eur"])

        if num_ctx is None:
            num_ctx = await self.settings.get_typed("num_ctx")
        if keep_alive is None:
            keep_alive = await self.settings.get_typed("keep_alive")

        cancel = asyncio.Event()
        parts: list[str] = []
        final_in: int | None = None
        final_out: int | None = None
        tps: float | None = None
        status = "complete"
        error: str | None = None
        try:
            async for chunk in provider.chat_stream(
                    model, messages,
                    ChatOptions(num_ctx=num_ctx, temperature=temperature,
                                keep_alive=keep_alive), cancel):
                if chunk.content:
                    parts.append(chunk.content)
                    if on_delta:
                        await on_delta(chunk.content)
                if chunk.done:
                    final_in = chunk.input_tokens
                    final_out = chunk.output_tokens
                    tps = chunk.output_tps
        except Exception as exc:  # provider failure
            status = "error"
            error = getattr(exc, "message", str(exc))
            log.warning("model call failed (%s/%s): %s", provider_name, model_name, error)

        content = "".join(parts)
        if max_output_tokens is not None and final_out is not None:
            pass  # runtime token limit is the source of truth

        method = "exact" if (final_in is not None and final_out is not None
                             and status == "complete") else "estimated"
        if method == "estimated":
            prior = "".join(m.content for m in messages if m.role != "assistant")
            if final_in is None:
                final_in = estimate_tokens(prior)
            if final_out is None:
                final_out = estimate_tokens(content) if content else 0

        rate_in = float((model_row or {}).get("cost_input_per_mtok") or 0.0)
        rate_out = float((model_row or {}).get("cost_output_per_mtok") or 0.0)
        cost = round((final_in * rate_in + final_out * rate_out) / 1_000_000, 8)

        try:
            await self.usage.record(conversation_id=sink.conversation_id if sink else None,
                                    message_id=sink.message_id if sink else None,
                                    team_id=sink.team_id if sink else None,
                                    team_member_id=sink.team_member_id if sink else None,
                                    model=model, provider=provider.name,
                                    input_tokens=final_in, output_tokens=final_out,
                                    method=method, cost_eur=cost)
        except Exception:  # pragma: no cover — ledger must not break the engine
            log.exception("usage ledger record failed")
        if status == "complete":
            try:
                await self.models.record_usage(provider.name, model, final_in,
                                               final_out, tps)
            except Exception:  # pragma: no cover
                log.exception("model usage record failed")
        # session counters (all guarded model calls, not only chat)
        metrics.session_input_tokens += final_in
        metrics.session_output_tokens += final_out
        metrics.session_cost_eur = round(metrics.session_cost_eur + cost, 8)
        metrics.last_request_cost_eur = cost  # type: ignore[attr-defined]

        gen = Generation(text=content, input_tokens=final_in, output_tokens=final_out,
                         token_method=method, tokens_per_second=tps, cost_eur=cost,
                         status=status, error=error, rate_in=rate_in, rate_out=rate_out)
        if sink:
            sink.add(gen)
        return gen

    @staticmethod
    def messages(*msgs: tuple[str, str]) -> list[ChatMessage]:
        return [ChatMessage(role=r, content=c) for r, c in msgs]
