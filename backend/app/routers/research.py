"""Research Mode API (P6) — web-grounded answers with citations over SSE.

Stream events: meta → status(searching|fetching|answering) → sources →
note* (dropped sources) → delta* → citations → usage → done (or error).
The answer pass is CostGuard-gated; the whole mode is gated behind the
``network:fetch`` capability (Settings → Agent permissions).
"""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from ..core.errors import NotFound
from ..schemas import ResearchQueryRequest

log = logging.getLogger("aicc.api.research")
router = APIRouter(prefix="/research", tags=["research"])

SSE_HEADERS = {"Cache-Control": "no-cache", "Connection": "keep-alive",
               "X-Accel-Buffering": "no"}


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


@router.post("/query")
async def research_query(body: ResearchQueryRequest,
                         request: Request) -> StreamingResponse:
    svc = request.app.state.services

    async def gen():
        rid: int | None = None
        inner = svc.research.stream_query(
            question=body.question, provider_name=body.provider,
            model_name=body.model)
        try:
            async for event in inner:
                if rid is None and event.get("research_id") is not None:
                    rid = event.get("research_id")
                if await request.is_disconnected():
                    if rid is not None:
                        svc.research.stop_run(rid)
                    break
                yield _sse(event)
        except Exception as exc:
            yield _sse({"type": "error",
                        "code": getattr(exc, "code", "INTERNAL_ERROR"),
                        "message": getattr(exc, "message",
                                           "Research query failed unexpectedly."),
                        "status_code": getattr(exc, "status_code", 500)})
        finally:
            await inner.aclose()

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers=SSE_HEADERS)


@router.get("/history")
async def research_history(request: Request, limit: int = 30) -> dict:
    svc = request.app.state.services
    limit = max(1, min(limit, 100))
    runs = await svc.research.repo.list(limit=limit)
    return {"runs": runs, "count": len(runs)}


@router.get("/{research_id}")
async def research_get(research_id: int, request: Request) -> dict:
    svc = request.app.state.services
    run = await svc.research.repo.get(research_id)
    if run is None:
        raise NotFound(f"Research run {research_id} not found.",
                       code="RESEARCH_NOT_FOUND")
    return {"run": run}


@router.post("/{research_id}/stop")
async def research_stop(research_id: int, request: Request) -> dict:
    svc = request.app.state.services
    if not svc.research.stop_run(research_id):
        raise NotFound(f"No active research run with id {research_id}.",
                       code="RUN_NOT_FOUND")
    return {"stopped": True, "research_id": research_id}
