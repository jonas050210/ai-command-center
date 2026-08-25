"""Team Mode API (P5) — multi-model pipelines over SSE.

Stream events: team_meta → (member_start → member_delta* → member_done)*;
executor members forward their full agent event stream as member_event
(approvals use the same /api/agent/approvals endpoints); verdict; usage;
team_done. (or error)
"""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from ..core.errors import NotFound
from ..schemas import TeamCreateRequest, TeamRunRequest

log = logging.getLogger("aicc.api.team")
router = APIRouter(prefix="/team", tags=["team"])

SSE_HEADERS = {"Cache-Control": "no-cache", "Connection": "keep-alive",
               "X-Accel-Buffering": "no"}


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


@router.get("")
async def list_teams(request: Request) -> dict:
    svc = request.app.state.services
    teams = await svc.team.teams.list()
    return {"teams": teams, "count": len(teams)}


@router.post("", status_code=201)
async def create_team(body: TeamCreateRequest, request: Request) -> dict:
    svc = request.app.state.services
    team = await svc.team.create_team(
        body.name, [m.model_dump() for m in body.members])
    team["members"] = await svc.team.teams.members_of(team["id"])
    return {"team": team}


@router.get("/{team_id}")
async def get_team(team_id: int, request: Request) -> dict:
    svc = request.app.state.services
    team = await svc.team.get_team(team_id)
    runs = await svc.team.team_runs.list(team_id=team_id, limit=20)
    return {"team": team, "runs": runs}


@router.delete("/{team_id}")
async def delete_team(team_id: int, request: Request) -> dict:
    svc = request.app.state.services
    deleted = await svc.team.teams.delete(team_id)
    if not deleted:
        raise NotFound(f"Team {team_id} not found.", code="TEAM_NOT_FOUND")
    return {"deleted": True, "team_id": team_id}


@router.post("/{team_id}/runs")
async def start_team_run(team_id: int, body: TeamRunRequest,
                         request: Request) -> StreamingResponse:
    svc = request.app.state.services

    async def gen():
        run_id: str | None = None
        inner = svc.team.stream_run(team_id=team_id, task=body.task)
        try:
            async for event in inner:
                if run_id is None and event.get("run_id"):
                    run_id = event.get("run_id")
                if await request.is_disconnected():
                    if run_id:
                        svc.team.stop_run(run_id)
                    break
                yield _sse(event)
        except Exception as exc:
            yield _sse({"type": "error",
                        "code": getattr(exc, "code", "INTERNAL_ERROR"),
                        "message": getattr(exc, "message", "Team run failed unexpectedly."),
                        "status_code": getattr(exc, "status_code", 500)})
        finally:
            await inner.aclose()

    return StreamingResponse(gen(), media_type="text/event-stream", headers=SSE_HEADERS)


@router.get("/runs/{run_id}")
async def get_team_run(run_id: str, request: Request) -> dict:
    svc = request.app.state.services
    run = await svc.team.team_runs.get(run_id)
    if run is None:
        raise NotFound(f"Team run '{run_id}' not found.", code="TEAM_RUN_NOT_FOUND")
    return {"run": run}


@router.post("/runs/{run_id}/stop")
async def stop_team_run(run_id: str, request: Request) -> dict:
    svc = request.app.state.services
    if not svc.team.stop_run(run_id):
        raise NotFound(f"No active team run with id '{run_id}'.", code="RUN_NOT_FOUND")
    return {"stopped": True, "run_id": run_id}
