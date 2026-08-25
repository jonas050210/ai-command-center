"""Agent API (P3) — real agent runs over SSE.

Event stream: meta → note* → (step → delta* → tool_call* →
approval_required? → approval_decided? → tool_result*)* → usage → done
 (or error / done status=stopped|denied).
"""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from ..core.errors import NotFound
from ..schemas import AgentRunRequest, ApprovalDecisionRequest

log = logging.getLogger("aicc.api.agent")
router = APIRouter(prefix="/agent", tags=["agent"])

SSE_HEADERS = {"Cache-Control": "no-cache", "Connection": "keep-alive",
               "X-Accel-Buffering": "no"}


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


@router.post("/runs")
async def start_run(body: AgentRunRequest, request: Request) -> StreamingResponse:
    svc = request.app.state.services

    async def gen():
        run_id: str | None = None
        inner = svc.agent.stream_run(task=body.task, provider_name=body.provider,
                                     model_name=body.model, skills_text=body.skills,
                                     project_id=body.project_id, mode=body.mode)
        try:
            async for event in inner:
                if run_id is None and event.get("run_id"):
                    run_id = event.get("run_id")
                if await request.is_disconnected():
                    if run_id:
                        svc.agent.runs_manager.stop(run_id)
                    break
                yield _sse(event)
        except Exception as exc:
            yield _sse({"type": "error",
                        "code": getattr(exc, "code", "INTERNAL_ERROR"),
                        "message": getattr(exc, "message", "Agent run failed unexpectedly."),
                        "status_code": getattr(exc, "status_code", 500)})
        finally:
            await inner.aclose()

    return StreamingResponse(gen(), media_type="text/event-stream", headers=SSE_HEADERS)


@router.post("/runs/{run_id}/stop")
async def stop_run(run_id: str, request: Request) -> dict:
    svc = request.app.state.services
    stopped = svc.agent.runs_manager.stop(run_id)
    if not stopped:
        raise NotFound(f"No active agent run with id '{run_id}'.",
                       code="RUN_NOT_FOUND")
    return {"stopped": True, "run_id": run_id}


@router.get("/runs")
async def list_runs(request: Request, limit: int = 30) -> dict:
    svc = request.app.state.services
    rows = await svc.agent.runs.list(limit=min(limit, 100))
    return {"runs": rows, "count": len(rows)}


@router.get("/runs/{run_id}")
async def get_run(run_id: str, request: Request) -> dict:
    svc = request.app.state.services
    run = await svc.agent.runs.get(run_id)
    if run is None:
        raise NotFound(f"Agent run '{run_id}' not found.")
    steps = await svc.agent.runs.steps(run_id)
    approvals = await svc.agent.approvals.for_run(run_id)
    return {"run": run, "steps": steps, "approvals": approvals}


@router.get("/runs/{run_id}/snapshot")
async def get_run_snapshot(run_id: str, request: Request) -> dict:
    svc = request.app.state.services
    run = await svc.agent.runs.get(run_id)
    if run is None:
        raise NotFound(f"Agent run '{run_id}' not found.")
    return svc.agent.snapshot_info(run_id)


@router.post("/runs/{run_id}/undo")
async def undo_agent_run(run_id: str, request: Request) -> dict:
    svc = request.app.state.services
    return await svc.agent.undo_run(run_id)


@router.post("/approvals/{approval_id}")
async def decide_approval(approval_id: str, body: ApprovalDecisionRequest,
                          request: Request) -> dict:
    svc = request.app.state.services
    return await svc.agent.decide_approval(approval_id, body.approve)


@router.get("/approvals/pending")
async def pending_approvals(request: Request) -> dict:
    svc = request.app.state.services
    rows = await svc.agent.approvals.pending()
    return {"approvals": rows}


@router.get("/capabilities")
async def capabilities(request: Request) -> dict:
    svc = request.app.state.services
    return {"capabilities": await svc.agent.capabilities_state()}


@router.get("/tools")
async def tools(request: Request) -> dict:
    svc = request.app.state.services
    return {"tools": svc.agent.tools.describe_all()}


@router.get("/executions")
async def executions(request: Request, limit: int = 50) -> dict:
    """Security audit log — every tool execution, denial and failure."""
    svc = request.app.state.services
    rows = await svc.executions_repo.list(limit=min(limit, 200))
    return {"executions": rows, "count": len(rows)}
