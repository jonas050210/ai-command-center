"""Compare Mode API (P4) — multiplexed SSE: one prompt, N models."""
from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from ..schemas import CompareRunRequest

log = logging.getLogger("aicc.api.compare")
router = APIRouter(prefix="/compare", tags=["compare"])

SSE_HEADERS = {"Cache-Control": "no-cache", "Connection": "keep-alive",
               "X-Accel-Buffering": "no"}


@router.post("/runs")
async def start_compare(body: CompareRunRequest, request: Request) -> StreamingResponse:
    svc = request.app.state.services

    async def gen():
        cancel = asyncio.Event()
        inner = svc.compare.stream(body.prompt, body.models, cancel)
        try:
            async for event in inner:
                if await request.is_disconnected():
                    cancel.set()
                    break
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except Exception as exc:
            yield f"data: {json.dumps({'type': 'error', 'code': getattr(exc, 'code', 'INTERNAL_ERROR'), 'message': getattr(exc, 'message', 'Compare run failed.')}, ensure_ascii=False)}\n\n"
        finally:
            cancel.set()
            await inner.aclose()

    return StreamingResponse(gen(), media_type="text/event-stream", headers=SSE_HEADERS)
