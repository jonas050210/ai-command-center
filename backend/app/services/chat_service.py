"""Chat service — orchestrates real streaming chat end to end.

Pipeline per request:
  resolve model → CostGuard (BEFORE any provider traffic) → persist
  user message → build history → stream from provider → persist
  assistant message with EXACT token counts → ledger (usage_events,
  conversation totals, model totals, session metrics).

Streams are cancellable server-side via ``RequestManager``.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any, AsyncIterator

from ..core.errors import BadRequest, NotFound
from ..db.repo import (ConversationsRepo, MessagesRepo, ModelsRepo, UsageRepo)
from ..observability.metrics import metrics
from ..providers.base import ChatMessage, ChatOptions
from .cost_guard import CostGuard
from .model_router import ModelRouter
from .settings_service import SettingsService
from .tokens import estimate_tokens

log = logging.getLogger("aicc.chat")

AUTO_TITLE_MAX_WORDS = 6


class RequestManager:
    """Tracks in-flight chat streams so /api/chat/stop can cancel them."""

    def __init__(self):
        self._active: dict[str, asyncio.Event] = {}

    def start(self, request_id: str) -> asyncio.Event:
        event = asyncio.Event()
        self._active[request_id] = event
        return event

    def finish(self, request_id: str) -> None:
        self._active.pop(request_id, None)

    def stop(self, request_id: str) -> bool:
        event = self._active.get(request_id)
        if event is None:
            return False
        event.set()
        return True


class ChatService:
    def __init__(self, *, conversations: ConversationsRepo, messages: MessagesRepo,
                 usage: UsageRepo, models: ModelsRepo, router: ModelRouter,
                 guard: CostGuard, settings: SettingsService,
                 requests: RequestManager):
        self.conversations = conversations
        self.messages = messages
        self.usage = usage
        self.models = models
        self.router = router
        self.guard = guard
        self.settings = settings
        self.requests = requests

    # ── helpers ──────────────────────────────────────────────────────
    async def _build_history(self, conversation: dict,
                             upto_message_id: str | None = None) -> list[ChatMessage]:
        rows = await self.messages.list_for(conversation["id"])
        history: list[ChatMessage] = []
        system_parts = [p for p in (
            conversation.get("system_prompt"),
            await self.settings.get_typed("custom_instructions"),
        ) if p and p.strip()]
        if system_parts:
            history.append(ChatMessage(role="system", content="\n\n".join(system_parts)))
        for row in rows:
            if upto_message_id is not None and row["id"] == upto_message_id:
                break
            if row["role"] == "assistant" and row["status"] not in ("complete", "stopped"):
                continue
            if row["role"] in ("user", "assistant"):
                history.append(ChatMessage(role=row["role"], content=row["content"]))
        return history

    # ── main entry points ────────────────────────────────────────────
    async def stream_completion(self, *, conversation_id: str | None, content: str,
                                provider_name: str | None, model_name: str | None,
                                system_prompt: str | None,
                                temperature: float | None,
                                project_id: int | None = None) -> AsyncIterator[dict[str, Any]]:
        if not content.strip():
            raise BadRequest("Message content must not be empty.")

        provider, model, model_row = await self.router.resolve(provider_name, model_name)
        totals = await self.usage.totals()
        # HARD €0 GUARD — runs before ANY provider network request.
        await self.guard.guard_request(provider, model, model_row,
                                       total_spent_eur=totals["cost_eur"])

        # conversation
        conversation: dict | None = None
        if conversation_id:
            conversation = await self.conversations.get(conversation_id)
            if conversation is None:
                raise NotFound(f"Conversation '{conversation_id}' not found.")
            await self.conversations.update(conversation_id, model=model,
                                            provider=provider.name)
            conversation = await self.conversations.get(conversation_id)
        else:
            title = content.strip().splitlines()[0][:60] or "New chat"
            conversation = await self.conversations.create(
                title=title, model=model, provider=provider.name,
                system_prompt=system_prompt, project_id=project_id)

        user_msg = await self.messages.create(conversation["id"], "user", content)
        await self.conversations.touch(conversation["id"])
        history = await self._build_history(conversation)
        if not history or history[-1].role != "user" or history[-1].content != content:
            history.append(ChatMessage(role="user", content=content))

        first_exchange = (await self.usage.totals())["events"] == 0 or \
            len([m for m in await self.messages.list_for(conversation["id"])
                 if m["role"] == "assistant"]) == 0

        async for event in self._stream(conversation=conversation, provider=provider,
                                        model=model, model_row=model_row,
                                        history=history, temperature=temperature,
                                        assistant_message=None, user_message=user_msg,
                                        auto_title=first_exchange):
            yield event

    async def stream_regenerate(self, *, assistant_message_id: str,
                                temperature: float | None) -> AsyncIterator[dict[str, Any]]:
        target = await self.messages.get(assistant_message_id)
        if target is None:
            raise NotFound(f"Message '{assistant_message_id}' not found.")
        if target["role"] != "assistant":
            raise BadRequest("Only assistant messages can be regenerated.")
        conversation = await self.conversations.get(target["conversation_id"])
        if conversation is None:
            raise NotFound("Conversation not found.")

        provider, model, model_row = await self.router.resolve(
            conversation.get("provider"), conversation.get("model"))
        totals = await self.usage.totals()
        await self.guard.guard_request(provider, model, model_row,
                                       total_spent_eur=totals["cost_eur"])

        history = await self._build_history(conversation, upto_message_id=assistant_message_id)
        async for event in self._stream(conversation=conversation, provider=provider,
                                        model=model, model_row=model_row,
                                        history=history, temperature=temperature,
                                        assistant_message=target, user_message=None,
                                        auto_title=False):
            yield event

    # ── core streaming ───────────────────────────────────────────────
    async def _stream(self, *, conversation: dict, provider, model: str,
                      model_row: dict | None, history: list[ChatMessage],
                      temperature: float | None, assistant_message: dict | None,
                      user_message: dict | None,
                      auto_title: bool) -> AsyncIterator[dict[str, Any]]:
        request_id = uuid.uuid4().hex
        cancel = self.requests.start(request_id)
        num_ctx = await self.settings.get_typed("num_ctx")
        keep_alive = await self.settings.get_typed("keep_alive")
        assistant = assistant_message or await self.messages.create(
            conversation["id"], "assistant", "", model=model,
            provider=provider.name, status="streaming")

        metrics.chat_requests += 1
        yield {"type": "meta", "request_id": request_id,
               "conversation_id": conversation["id"],
               "user_message_id": user_message["id"] if user_message else None,
               "assistant_message_id": assistant["id"],
               "model": model, "provider": provider.name}

        content_parts: list[str] = []
        final_in: int | None = None
        final_out: int | None = None
        tps: float | None = None
        status = "complete"
        error_text: str | None = None

        try:
            async for chunk in provider.chat_stream(
                    model, history, ChatOptions(num_ctx=num_ctx, temperature=temperature,
                                                keep_alive=keep_alive), cancel):
                if cancel.is_set():
                    status = "stopped"
                    break
                if chunk.content:
                    content_parts.append(chunk.content)
                    yield {"type": "delta", "content": chunk.content}
                if chunk.done:
                    final_in = chunk.input_tokens
                    final_out = chunk.output_tokens
                    tps = chunk.output_tps
        except Exception as exc:  # provider failure mid-stream
            status = "error"
            error_text = getattr(exc, "message", str(exc))
            metrics.chat_errors += 1
            log.warning("chat stream failed: %s", error_text)
        finally:
            self.requests.finish(request_id)

        content = "".join(content_parts)
        method = "exact" if (final_in is not None and final_out is not None
                             and status != "stopped") else "estimated"
        if method == "estimated":
            prior = "".join(m.content for m in history if m.role != "assistant")
            final_in = final_in if final_in is not None else estimate_tokens(prior)
            final_out = final_out if final_out is not None else estimate_tokens(content)

        rate_in = float((model_row or {}).get("cost_input_per_mtok") or 0.0)
        rate_out = float((model_row or {}).get("cost_output_per_mtok") or 0.0)
        cost = round((final_in * rate_in + final_out * rate_out) / 1_000_000, 8)

        await self.messages.finalize(assistant["id"], content=content, status=status,
                                     input_tokens=final_in, output_tokens=final_out,
                                     method=method, error=error_text)
        await self.usage.record(conversation_id=conversation["id"],
                                message_id=assistant["id"], model=model,
                                provider=provider.name, input_tokens=final_in,
                                output_tokens=final_out, method=method, cost_eur=cost)
        await self.conversations.add_tokens(conversation["id"], final_in, final_out)
        if status == "complete":
            await self.models.record_usage(provider.name, model, final_in, final_out, tps)
        if status == "error":
            yield {"type": "error", "code": "PROVIDER_ERROR", "message": error_text}
        else:
            yield {"type": "usage", "input_tokens": final_in, "output_tokens": final_out,
                   "total_tokens": final_in + final_out, "method": method,
                   "tokens_per_second": round(tps, 1) if tps else None,
                   "cost_eur": cost}
            yield {"type": "done", "assistant_message_id": assistant["id"], "status": status}
            if auto_title and status == "complete":
                asyncio.create_task(self._auto_title(conversation["id"], provider, model,
                                                     num_ctx, keep_alive, content,
                                                     history[-1].content if history else ""))

    async def _auto_title(self, conversation_id: str, provider, model: str,
                          num_ctx: int, keep_alive: str, reply: str,
                          question: str) -> None:
        """Best-effort conversation title from the real local model.

        Defence in depth: the title call re-checks the CostGuard before any
        provider traffic (same model/pricing as the already-guarded call).
        """
        try:
            totals = await self.usage.totals()
            await self.guard.guard_request(provider, model, None,
                                           total_spent_eur=totals["cost_eur"])
            prompt = (f"Write a short title (max {AUTO_TITLE_MAX_WORDS} words, plain text, "
                      f"no quotes) for a chat where the user asked: {question[:300]!r}")
            parts: list[str] = []
            async for chunk in provider.chat_stream(
                    model, [ChatMessage(role="user", content=prompt)],
                    ChatOptions(num_ctx=num_ctx, keep_alive=keep_alive), asyncio.Event()):
                if chunk.content:
                    parts.append(chunk.content)
                if chunk.done:
                    break
            title = " ".join("".join(parts).strip().split())[:60].strip(' ".*#`')
            if title:
                await self.conversations.update(conversation_id, title=title)
                log.info("auto-titled conversation %s → %s", conversation_id, title)
        except Exception as exc:  # never break chat for a title
            log.debug("auto-title failed (ignored): %s", exc)
