"""Research Mode (P6) — web-grounded answers with citations.

Pipeline: search (ddgs) → fetch & extract top sources (SSRF-guarded) →
an LLM answers with numbered citations. Sources that fail to fetch are
dropped and said so; if NO source fetched, the run fails honestly instead
of hallucinating from nothing. Every run is persisted to the ``research``
table with its sources. The answer pass goes through ModelRouter +
CostGuard like every other token spend.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from typing import Any

from ..core.errors import BadRequest
from ..db.repo import ResearchRepo, UsageRepo
from ..providers.base import ChatMessage, ChatOptions
from ..services.cost_guard import CostGuard
from ..services.model_router import ModelRouter
from ..services.settings_service import SettingsService
from .web import gather_pages, web_search

log = logging.getLogger("aicc.research")

MAX_SOURCES_PROMPT = 4
PER_SOURCE_CHARS = 3000

ANSWER_PROMPT = """Answer the user's research question using ONLY the sources below.

Rules:
- Cite claims inline with [n] matching the source numbers.
- If the sources don't cover a part, say so — never invent facts.
- Structure: short answer first, then supporting details.
- End with nothing else — the app renders the source list itself.

QUESTION: {question}

SOURCES:
{sources}
"""


class ResearchRunManager:
    """Cooperative cancellation for research runs."""

    def __init__(self):
        self._cancels: dict[int, asyncio.Event] = {}

    def start(self, rid: int) -> asyncio.Event:
        ev = asyncio.Event()
        self._cancels[rid] = ev
        return ev

    def finish(self, rid: int) -> None:
        self._cancels.pop(rid, None)

    def stop(self, rid: int) -> bool:
        ev = self._cancels.get(rid)
        if ev is None:
            return False
        ev.set()
        return True


class ResearchService:
    def __init__(self, *, repo: ResearchRepo, usage: UsageRepo,
                 router: ModelRouter, guard: CostGuard,
                 settings: SettingsService, run_manager: ResearchRunManager):
        self.repo = repo
        self.usage = usage
        self.router = router
        self.guard = guard
        self.settings = settings
        self.run_manager = run_manager

    async def enabled(self) -> bool:
        return bool(await self.settings.get_typed("cap_network_fetch"))

    def stop_run(self, rid: int) -> bool:
        return self.run_manager.stop(rid)

    async def stream_query(self, *, question: str, provider_name: str | None,
                           model_name: str | None) -> AsyncIterator[dict[str, Any]]:
        question = question.strip()
        if not question:
            raise BadRequest("Research question must not be empty.")
        if not await self.enabled():
            raise BadRequest(
                "Research is disabled: the 'network:fetch' capability is off. "
                "Enable it in Settings → Agent permissions.", code="RESEARCH_DISABLED")

        run = await self.repo.create(question)
        rid: int = run["id"]
        cancel = self.run_manager.start(rid)
        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        worker = asyncio.create_task(self._worker(
            queue=queue, rid=rid, question=question,
            provider_name=provider_name, model_name=model_name, cancel=cancel))
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
            self.run_manager.finish(rid)

    async def _worker(self, *, queue: asyncio.Queue, rid: int, question: str,
                      provider_name: str | None, model_name: str | None,
                      cancel: asyncio.Event) -> None:
        t0 = time.monotonic()
        status = "error"
        answer = ""
        citations: list[dict[str, Any]] = []

        async def emit(ev: dict[str, Any]) -> None:
            await queue.put(ev)

        try:
            await emit({"type": "meta", "research_id": rid,
                        "run_id": rid, "question": question})

            # ── 1. search ──
            await emit({"type": "status", "stage": "searching",
                        "message": f"Searching the web: {question[:80]}"})
            results = await web_search(question, max_results=6)
            if cancel.is_set():
                status = "stopped"
                return
            if not results:
                raise BadRequest("The search returned no results — try rephrasing.",
                                 code="RESEARCH_NO_RESULTS")
            await emit({"type": "sources", "sources": [
                {"index": i + 1, "title": r.title, "url": r.url,
                 "snippet": r.snippet}
                for i, r in enumerate(results[:MAX_SOURCES_PROMPT + 2])]})

            # ── 2. fetch & extract (SSRF-guarded, size-capped) ──
            await emit({"type": "status", "stage": "fetching",
                        "message": "Fetching and extracting sources…"})
            pages = await gather_pages(results, max_pages=MAX_SOURCES_PROMPT)
            good = [p for p in pages if not p.error and p.text.strip()]
            dropped = [p for p in pages if p.error or not p.text.strip()]
            if cancel.is_set():
                status = "stopped"
                return
            if not good:
                raise BadRequest(
                    "No source could be read (all fetches failed or were blocked).",
                    code="RESEARCH_NO_CONTENT")
            for d in dropped:
                await emit({"type": "note", "level": "info",
                            "message": f"Source skipped: {d.url} "
                                       f"({d.error or 'no extractable text'})"})

            citations = [{"index": i + 1, "title": p.title or p.url, "url": p.url}
                         for i, p in enumerate(good)]
            sources_block = "\n\n".join(
                f"[{i + 1}] {p.title or p.url}\nURL: {p.url}\n"
                f"{p.text[:PER_SOURCE_CHARS]}"
                + ("…[truncated]" if p.chars > PER_SOURCE_CHARS or p.truncated else "")
                for i, p in enumerate(good))

            # ── 3. answer (CostGuard-gated, metered) ──
            provider, model, row = await self.router.resolve(provider_name,
                                                             model_name)
            totals = await self.usage.totals()
            await self.guard.guard_request(provider, model, row,
                                           total_spent_eur=totals["cost_eur"])
            await emit({"type": "status", "stage": "answering",
                        "message": f"Synthesizing with {model}…"})
            keep_alive = await self.settings.get_typed("keep_alive")
            parts: list[str] = []
            final_in: int | None = None
            final_out: int | None = None
            async for chunk in provider.chat_stream(
                    model,
                    [ChatMessage(role="user", content=ANSWER_PROMPT.format(
                        question=question, sources=sources_block))],
                    ChatOptions(keep_alive=keep_alive), cancel):
                if cancel.is_set():
                    break
                if chunk.content:
                    parts.append(chunk.content)
                    await emit({"type": "delta", "content": chunk.content})
                if chunk.done:
                    final_in = chunk.input_tokens
                    final_out = chunk.output_tokens

            method = "exact" if final_in is not None else "estimated"
            await self.usage.record(
                conversation_id=None, message_id=None, model=model,
                provider=provider.name, input_tokens=final_in or 0,
                output_tokens=final_out or 0, method=method, cost_eur=0.0)
            answer = "".join(parts).strip()
            status = "stopped" if cancel.is_set() else "complete"
            await emit({"type": "citations", "citations": citations})
            await emit({"type": "usage", "input_tokens": final_in or 0,
                        "output_tokens": final_out or 0, "method": method,
                        "model": model, "provider": provider.name,
                        "elapsed_s": round(time.monotonic() - t0, 1)})
        except asyncio.CancelledError:
            status = "stopped"
        except Exception as exc:
            status = "error"
            await emit({"type": "error",
                        "code": getattr(exc, "code", "RESEARCH_ERROR"),
                        "message": getattr(exc, "message", str(exc))})
        finally:
            await self.repo.finish(rid, status=status, result=answer,
                                   sources=citations)
            await emit({"type": "done", "research_id": rid, "status": status,
                        "answer": answer, "citations": citations})
            await queue.put(None)
