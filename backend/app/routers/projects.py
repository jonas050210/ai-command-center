"""Projects API (P4) — named sandboxed workspaces for agent runs.

Deletion is deliberately absent: archiving only (no silent data loss).
"""
from __future__ import annotations

from fastapi import APIRouter, Request

from ..schemas import ProjectCreateRequest, ProjectUpdateRequest

router = APIRouter(prefix="/projects", tags=["projects"])


@router.get("")
async def list_projects(request: Request, archived: bool = False) -> dict:
    svc = request.app.state.services
    projects = await svc.projects.list(include_archived=archived)
    return {"projects": projects, "count": len(projects)}


@router.post("", status_code=201)
async def create_project(body: ProjectCreateRequest, request: Request) -> dict:
    svc = request.app.state.services
    row = await svc.projects.create(body.name, body.description)
    return {"project": row}


@router.get("/{pid}")
async def get_project(pid: int, request: Request) -> dict:
    svc = request.app.state.services
    row = await svc.projects.get(pid)
    return {"project": row, "listing": svc.projects.listing(row)}


@router.patch("/{pid}")
async def update_project(pid: int, body: ProjectUpdateRequest, request: Request) -> dict:
    svc = request.app.state.services
    row = await svc.projects.rename(pid, name=body.name, description=body.description)
    if body.status is not None:
        row = await svc.projects.set_status(pid, body.status)
    return {"project": row}


@router.get("/{pid}/runs")
async def project_runs(pid: int, request: Request) -> dict:
    """Agent runs that targeted this project (most recent first)."""
    svc = request.app.state.services
    await svc.projects.get(pid)  # 404 if unknown
    rows = await svc.agent.runs.list_for_project(pid)
    return {"runs": rows, "count": len(rows)}
