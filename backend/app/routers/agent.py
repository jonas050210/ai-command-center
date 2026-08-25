"""Agent Mode API — start/runs/state/tools. Everything streams as SSE."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from ..core.errors import AppError, NotFound
from ..schemas import AgentRunRequest
from .sse import SSE_HEADERS, sse

log = logging.getLogger("aicc.api.agent")
router = APIRouter(prefix="/agent", tags=["agent"])


@router.post("/runs")
async def start_agent(body: AgentRunRequest, request: Request) -> StreamingResponse:
    svc = request.app.state.services

    async def gen():
        try:
            async for event in svc.agent.run(task=body.task,
                                             project_id=body.project_id,
                                             provider_name=body.provider,
                                             model_name=body.model):
                yield sse(event)
        except AppError as exc:
            yield sse({"type": "error", "code": exc.code, "message": exc.message,
                       "status_code": exc.status_code})
        except Exception:  # pragma: no cover
            log.exception("agent run failed", exc_info=True)
            yield sse({"type": "error", "code": "INTERNAL_ERROR",
                       "message": "Agent run failed unexpectedly.", "status_code": 500})

    return StreamingResponse(gen(), media_type="text/event-stream", headers=SSE_HEADERS)


@router.get("/runs")
async def list_runs(request: Request) -> dict:
    svc = request.app.state.services
    return {"runs": await svc.agent_repo.list()}


@router.get("/runs/{run_id}")
async def get_run(run_id: int, request: Request) -> dict:
    svc = request.app.state.services
    run = await svc.agent_repo.get(run_id)
    if run is None:
        raise NotFound(f"Agent run '{run_id}' not found.")
    run["steps"] = await svc.agent_repo.steps(run_id)
    return run


@router.get("/capabilities")
async def capabilities(request: Request) -> dict:
    svc = request.app.state.services
    from ..tools.runner import ALLOWED_COMMANDS
    return {
        "workspace_root": str(svc.settings.resolved_workspace_root),
        "file_tools": ["read_file", "write_file", "edit_file", "search_files",
                       "list_files", "create_directory", "delete_file"],
        "commands": sorted(ALLOWED_COMMANDS),
        "security": "paths sandboxed, commands allowlisted, no shell, "
                    "timeouts enforced, everything audited",
    }
