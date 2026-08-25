"""Context window management (P2).

Every request budget is clamped to the model's REAL context length, and
histories that would overflow the *effective* window are compacted —
summarized by the active provider itself (real LLM summarization, with a
deterministic trim fallback). Compaction never mutates stored messages;
it only shapes the outgoing payload, and callers expose it honestly
(``compacted`` flag in the SSE meta event).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

from ..providers.base import ChatMessage, ChatOptions, Provider
from .tokens import estimate_tokens

log = logging.getLogger("aicc.context")

# share of the effective window reserved for the model's answer
RESERVE_RATIO = 0.25
# tail kept verbatim during compaction (recent turns stay exact)
TAIL_SHARE = 0.55

SummarizeFn = Callable[[list[ChatMessage]], Awaitable[str | None]]


def effective_num_ctx(settings_num_ctx: int, model_context_length: int | None) -> int:
    """Never request more context than the model actually has."""
    if model_context_length:
        return max(512, min(settings_num_ctx, int(model_context_length)))
    return settings_num_ctx


def message_tokens(m: ChatMessage) -> int:
    text = m.content or ""
    if m.tool_calls:
        import json
        text += json.dumps(m.tool_calls, default=str)
    return estimate_tokens(text) + 4


def total_tokens(messages: list[ChatMessage]) -> int:
    return sum(message_tokens(m) for m in messages)


async def summarize_middle(provider: Provider, model: str,
                           omitted: list[ChatMessage], keep_alive: str | None) -> str | None:
    """Summarize omitted history with the active provider. None on failure."""
    import asyncio

    transcript = "\n".join(
        f"{m.role.upper()}: {(m.content or '')[:1500]}" for m in omitted)
    prompt = (
        "Summarize this conversation excerpt in at most 12 bullet points, keeping "
        "decisions, file/code facts, tool outputs that matter, and open tasks. "
        "Be factual — this summary replaces the original text as context.\n\n"
        f"{transcript}")
    try:
        parts: list[str] = []
        async for chunk in provider.chat_stream(
                model, [ChatMessage(role="user", content=prompt)],
                ChatOptions(keep_alive=keep_alive), asyncio.Event()):
            if chunk.content:
                parts.append(chunk.content)
            if chunk.done:
                break
        summary = "".join(parts).strip()
        return summary[:4000] if summary else None
    except Exception as exc:
        log.debug("context summarization failed (trim fallback): %s", exc)
        return None


async def compact_messages(messages: list[ChatMessage], num_ctx: int,
                           summarize: SummarizeFn | None = None) -> tuple[list[ChatMessage], bool]:
    """Bring *messages* under the usable budget. Returns (messages, compacted).

    Layout preserved: leading system message(s) exact → one compaction
    marker (LLM summary or trim note) → recent tail exact.
    """
    budget = int(num_ctx * (1 - RESERVE_RATIO))
    if total_tokens(messages) <= budget:
        return messages, False

    head: list[ChatMessage] = []
    rest = list(messages)
    while rest and rest[0].role == "system":
        head.append(rest.pop(0))

    tail_budget = int(budget * TAIL_SHARE)
    tail: list[ChatMessage] = []
    acc = 0
    while rest:
        cand = rest[-1]
        t = message_tokens(cand)
        if tail and acc + t > tail_budget:
            break
        tail.insert(0, rest.pop())
        acc += t
    omitted = rest
    if not omitted:                        # tail alone is over budget → hard trim oldest tail
        return head + tail[-2:], True

    summary: str | None = None
    if summarize is not None:
        summary = await summarize(omitted)
    if summary is None:
        summary = (f"{len(omitted)} earlier message(s) were omitted to fit the "
                   f"{num_ctx}-token context window (tool outputs truncated).")
    marker = ChatMessage(
        role="user",
        content="[Context compacted — summary of earlier conversation]\n" + summary)
    log.info("context compacted: %d msgs omitted → summary (%d chars), tail kept: %d",
             len(omitted), len(summary), len(tail))
    return head + [marker] + tail, True
