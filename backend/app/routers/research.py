"""Research Mode API — real search + citations, optional local synthesis."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse, StreamingResponse

from ..core.errors import AppError, NotFound
from ..schemas import ResearchRunRequest
from .sse import SSE_HEADERS, sse

log = logging.getLogger("aicc.api.research")
router = APIRouter(prefix="/research", tags=["research"])


@router.post("/runs")
async def start_research(body: ResearchRunRequest, request: Request) -> StreamingResponse:
    svc = request.app.state.services

    async def gen():
        try:
            async for event in svc.research.run(query=body.query,
                                                project_id=body.project_id,
                                                synthesize=body.synthesize,
                                                provider_name=body.provider,
                                                model_name=body.model):
                yield sse(event)
        except AppError as exc:
            yield sse({"type": "error", "code": exc.code, "message": exc.message,
                       "status_code": exc.status_code})
        except Exception:  # pragma: no cover
            log.exception("research run failed", exc_info=True)
            yield sse({"type": "error", "code": "INTERNAL_ERROR",
                       "message": "Research run failed unexpectedly.",
                       "status_code": 500})

    return StreamingResponse(gen(), media_type="text/event-stream", headers=SSE_HEADERS)


@router.get("/runs")
async def list_runs(request: Request) -> dict:
    svc = request.app.state.services
    return {"runs": await svc.research_repo.list()}


@router.get("/runs/{rid}")
async def get_run(rid: int, request: Request) -> dict:
    svc = request.app.state.services
    import json
    row = await svc.research_repo.get(rid)
    if row is None:
        raise NotFound(f"Research '{rid}' not found.")
    row["sources"] = json.loads(row["sources_json"] or "[]")
    row.pop("sources_json", None)
    return row


@router.get("/runs/{rid}/export")
async def export_run(rid: int, request: Request) -> PlainTextResponse:
    svc = request.app.state.services
    md = await svc.research.export_markdown(rid)
    return PlainTextResponse(md, media_type="text/markdown")


@router.delete("/runs/{rid}")
async def delete_run(rid: int, request: Request) -> dict:
    svc = request.app.state.services
    if await svc.research_repo.get(rid) is None:
        raise NotFound(f"Research '{rid}' not found.")
    await svc.research_repo.delete(rid)
    return {"deleted": True, "id": rid}
