"""Team Mode API — start streams SSE; state, board, tasks, history via REST."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from ..core.errors import AppError, NotFound
from ..schemas import TeamRunRequest, TeamTaskPatch
from .sse import SSE_HEADERS, sse

log = logging.getLogger("aicc.api.team")
router = APIRouter(prefix="/team", tags=["team"])


@router.post("/runs")
async def start_team(body: TeamRunRequest, request: Request) -> StreamingResponse:
    svc = request.app.state.services

    async def gen():
        try:
            async for event in svc.team.run(task=body.task, model_names=body.models,
                                            provider_name=body.provider,
                                            roles_override=body.roles,
                                            project_id=body.project_id):
                yield sse(event)
        except AppError as exc:
            yield sse({"type": "error", "code": exc.code, "message": exc.message,
                       "status_code": exc.status_code})
        except Exception:  # pragma: no cover
            log.exception("team run failed", exc_info=True)
            yield sse({"type": "error", "code": "INTERNAL_ERROR",
                       "message": "Team run failed unexpectedly.", "status_code": 500})

    return StreamingResponse(gen(), media_type="text/event-stream", headers=SSE_HEADERS)


@router.get("/runs")
async def list_teams(request: Request) -> dict:
    svc = request.app.state.services
    return {"teams": await svc.teams_repo.list()}


@router.get("/runs/{team_id}")
async def get_team(team_id: int, request: Request) -> dict:
    svc = request.app.state.services
    team = await svc.teams_repo.get(team_id)
    if team is None:
        raise NotFound(f"Team run '{team_id}' not found.")
    return {
        **team,
        "members": await svc.teams_repo.members(team_id),
        "events": await svc.teams_repo.events(team_id, limit=500),
        "tasks": await svc.teams_repo.tasks(team_id),
        "tokens": await svc.teams_repo.token_totals(team_id),
    }


@router.get("/runs/{team_id}/board")
async def get_board(team_id: int, request: Request) -> dict:
    svc = request.app.state.services
    await _exists(svc, team_id)
    tasks = await svc.teams_repo.tasks(team_id)
    board = {"todo": [], "in_progress": [], "review": [], "done": []}
    for t in tasks:
        board.setdefault(t.get("status", "todo"), []).append(t)
    return {"board": board, "tasks": tasks}


@router.patch("/runs/{team_id}/tasks/{task_id}")
async def patch_task(team_id: int, task_id: int, body: TeamTaskPatch,
                     request: Request) -> dict:
    svc = request.app.state.services
    await _exists(svc, team_id)
    task = next((t for t in await svc.teams_repo.tasks(team_id)
                 if t["id"] == task_id), None)
    if task is None:
        raise NotFound(f"Task '{task_id}' not found in team '{team_id}'.")
    await svc.teams_repo.update_task(task_id, **body.model_dump(exclude_none=True))
    await svc.teams_repo.add_event(team_id, "board", "decision",
                                   f"manual board update: {task['title']}",
                                   actor="user")
    return {"updated": True, "task_id": task_id}


@router.get("/runs/{team_id}/export")
async def export_team(team_id: int, request: Request) -> dict:
    svc = request.app.state.services
    team = await svc.teams_repo.get(team_id)
    if team is None:
        raise NotFound(f"Team run '{team_id}' not found.")
    members = await svc.teams_repo.members(team_id)
    tokens = await svc.teams_repo.token_totals(team_id)
    return {
        "task": team["task"], "master_plan": team["master_plan"],
        "status": team["status"], "deliverable": team["deliverable"],
        "members": [{"model": m["model"], "role": m["role"],
                     "input_tokens": m["input_tokens"],
                     "output_tokens": m["output_tokens"]}
                    for m in members],
        "tokens": tokens,
        "export": {
            "format": "markdown",
            "content": _to_markdown(team, members, tokens),
        },
    }


def _to_markdown(team: dict, members: list[dict], tokens: dict) -> str:
    lines = [f"# Team Report: {team['name']}", "",
             f"**Status:** {team['status']}", "", "## Task", team["task"], "",
             "## Master Plan", team["master_plan"], "", "## Members"]
    lines += [f"- {m['model']} — {m['role']} ({m['input_tokens']} in / "
              f"{m['output_tokens']} out)" for m in members]
    lines += ["", f"**TEAM TOTAL:** {tokens['total_tokens']} tokens · "
                  f"COST €{tokens['cost_eur']:.2f}", "", "## Deliverable",
              team["deliverable"] or "(in progress)"]
    return "\n".join(lines)


async def _exists(svc, team_id: int) -> None:
    if await svc.teams_repo.get(team_id) is None:
        raise NotFound(f"Team run '{team_id}' not found.")
