"""Chat API — real streaming over Server-Sent Events (SSE).

Event stream JSON payloads: meta → delta* → usage → done
                          (or error / status=stopped).
"""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from ..core.errors import AppError, NotFound
from ..schemas import ChatCompletionRequest, ChatStopRequest, RegenerateRequest

log = logging.getLogger("aicc.api.chat")
router = APIRouter(prefix="/chat", tags=["chat"])

SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


@router.post("/completions")
async def completions(body: ChatCompletionRequest, request: Request) -> StreamingResponse:
    svc = request.app.state.services

    async def gen():
        try:
            async for event in svc.chat.stream_completion(
                    conversation_id=body.conversation_id, content=body.content,
                    provider_name=body.provider, model_name=body.model,
                    system_prompt=body.system_prompt, temperature=body.temperature):
                if await request.is_disconnected():
                    break
                yield _sse(event)
        except AppError as exc:
            yield _sse({"type": "error", "code": exc.code, "message": exc.message,
                        "status_code": exc.status_code, "details": exc.details})
        except Exception:  # pragma: no cover
            log.exception("chat completion failed")
            yield _sse({"type": "error", "code": "INTERNAL_ERROR",
                        "message": "Chat failed unexpectedly.", "status_code": 500})

    return StreamingResponse(gen(), media_type="text/event-stream", headers=SSE_HEADERS)


@router.post("/regenerate")
async def regenerate(body: RegenerateRequest, request: Request) -> StreamingResponse:
    svc = request.app.state.services

    async def gen():
        try:
            async for event in svc.chat.stream_regenerate(
                    assistant_message_id=body.message_id, temperature=body.temperature):
                if await request.is_disconnected():
                    break
                yield _sse(event)
        except AppError as exc:
            yield _sse({"type": "error", "code": exc.code, "message": exc.message,
                        "status_code": exc.status_code, "details": exc.details})
        except Exception:  # pragma: no cover
            log.exception("regenerate failed")
            yield _sse({"type": "error", "code": "INTERNAL_ERROR",
                        "message": "Regeneration failed unexpectedly.", "status_code": 500})

    return StreamingResponse(gen(), media_type="text/event-stream", headers=SSE_HEADERS)


@router.post("/stop")
async def stop(body: ChatStopRequest, request: Request) -> dict:
    svc = request.app.state.services
    stopped = svc.chat.requests.stop(body.request_id)
    if not stopped:
        raise NotFound(f"No active generation with id '{body.request_id}'.",
                       code="REQUEST_NOT_FOUND")
    return {"stopped": True, "request_id": body.request_id}
