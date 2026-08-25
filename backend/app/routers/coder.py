"""Coder Mode API (P11) — profile + sandboxed file tree / file read.

Mutations are deliberately absent: the agent gateway owns every write/exec.
"""
from __future__ import annotations

from fastapi import APIRouter, Request

from ..core.errors import BadRequest

router = APIRouter(prefix="/coder", tags=["coder"])


@router.get("/context")
async def coder_context(request: Request, project_id: int | None = None) -> dict:
    svc = request.app.state.services
    text = await svc.coder.context_pack(project_id)
    return {"context": text, "project_id": project_id}


@router.get("/profile")
async def coder_profile(request: Request) -> dict:
    svc = request.app.state.services
    rt = await svc.settings_service.as_dict()
    profile = await svc.coder.profile(rt["default_model"])
    return profile


@router.get("/tree")
async def coder_tree(request: Request, project_id: int | None = None,
                     path: str = ".") -> dict:
    svc = request.app.state.services
    root, project, rel_root = await svc.coder.resolve_scope(project_id)
    listing = svc.coder.tree(root, path)
    return {
        "project": ({"id": project["id"], "name": project["name"],
                     "root_path": project.get("root_path")} if project else None),
        "workspace_rel": rel_root,
        **listing,
    }


@router.get("/file")
async def coder_file(request: Request, path: str,
                     project_id: int | None = None) -> dict:
    if not (path or "").strip():
        raise BadRequest("Query parameter 'path' is required.", code="PATH_REQUIRED")
    svc = request.app.state.services
    root, project, rel_root = await svc.coder.resolve_scope(project_id)
    body = svc.coder.read_file(root, path)
    return {
        "project": ({"id": project["id"], "name": project["name"]} if project else None),
        "workspace_rel": rel_root,
        **body,
    }
