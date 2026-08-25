"""Compare Mode API — run models side by side, pick best, combine.

The run streams real generation deltas: each model's output is pushed to
the browser as it is produced (``delta`` events) plus per-answer token
summaries (``answer_done``).
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from ..core.errors import AppError, NotFound
from ..schemas import CompareRunRequest, CompareSelectRequest
from .sse import SSE_HEADERS, sse

log = logging.getLogger("aicc.api.compare")
router = APIRouter(prefix="/compare", tags=["compare"])


@router.post("/runs")
async def start_compare(body: CompareRunRequest, request: Request) -> StreamingResponse:
    svc = request.app.state.services
    queue: "asyncio.Queue" = asyncio.Queue()

    async def on_delta(event: dict) -> None:
        await queue.put(event)

    async def producer() -> None:
        try:
            async for event in svc.compare.run(prompt=body.prompt,
                                               model_names=body.models,
                                               provider_name=body.provider,
                                               project_id=body.project_id,
                                               on_delta=on_delta):
                await queue.put(event)
                if event.get("type") == "done":
                    break
        except AppError as exc:
            await queue.put({"type": "error", "code": exc.code,
                             "message": exc.message, "status_code": exc.status_code})
        except Exception:  # pragma: no cover
            log.exception("compare run failed", exc_info=True)
            await queue.put({"type": "error", "code": "INTERNAL_ERROR",
                             "message": "Compare run failed unexpectedly.",
                             "status_code": 500})
        finally:
            await queue.put(None)  # sentinel

    async def gen():
        task = asyncio.create_task(producer())
        try:
            while True:
                event = await queue.get()
                if event is None:
                    break
                yield sse(event)
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    return StreamingResponse(gen(), media_type="text/event-stream", headers=SSE_HEADERS)


@router.get("/runs")
async def list_runs(request: Request) -> dict:
    svc = request.app.state.services
    return {"runs": await svc.compare_repo.list()}


@router.get("/runs/{run_id}")
async def get_run(run_id: int, request: Request) -> dict:
    svc = request.app.state.services
    return await svc.compare.state(run_id)


@router.post("/runs/{run_id}/select")
async def select_answer(run_id: int, body: CompareSelectRequest,
                        request: Request) -> dict:
    svc = request.app.state.services
    return await svc.compare.select(run_id, body.answer_id)


@router.post("/runs/{run_id}/combine")
async def combine_answers(run_id: int, request: Request) -> dict:
    svc = request.app.state.services
    return await svc.compare.combine(run_id)


@router.delete("/runs/{run_id}")
async def delete_run(run_id: int, request: Request) -> dict:
    svc = request.app.state.services
    run = await svc.compare_repo.get(run_id)
    if run is None:
        raise NotFound(f"Compare run '{run_id}' not found.")
    await svc.db.execute("DELETE FROM compare_runs WHERE id=?", (run_id,))
    return {"deleted": True, "id": run_id}
