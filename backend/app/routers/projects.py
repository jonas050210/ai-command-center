"""Projects API — first-class projects: chats, files, tasks, settings."""
from __future__ import annotations

import json

from fastapi import APIRouter, Request

from ..core.errors import NotFound
from ..schemas import ProjectCreate, ProjectTaskCreate, ProjectUpdate
from ..workspace.paths import resolve_within

router = APIRouter(prefix="/projects", tags=["projects"])


def _serialize(project: dict) -> dict:
    try:
        settings = json.loads(project.get("settings_json") or "{}")
    except (TypeError, ValueError):
        settings = {}
    return {
        "id": project["id"], "name": project["name"],
        "description": project.get("description", ""),
        "root_path": project.get("root_path"),
        "status": project.get("status", "active"),
        "settings": settings,
        "task_count": project.get("task_count", 0),
        "chat_count": project.get("chat_count", 0),
        "created_at": project["created_at"], "updated_at": project["updated_at"],
    }


@router.get("")
async def list_projects(request: Request) -> dict:
    svc = request.app.state.services
    rows = await svc.projects_repo.list()
    return {"projects": [_serialize(r) for r in rows], "count": len(rows)}


@router.post("")
async def create_project(body: ProjectCreate, request: Request) -> dict:
    svc = request.app.state.services
    project = await svc.projects_repo.create(body.name, body.description)
    # sandboxed workspace dir for the project
    ws = svc.settings.resolved_workspace_root / "projects" / f"p{project['id']}"
    ws.mkdir(parents=True, exist_ok=True)
    await svc.projects_repo.update(project["id"],
                                   root_path=f"projects/p{project['id']}")
    return _serialize(await svc.projects_repo.get(project["id"]))


@router.get("/{pid}")
async def get_project(pid: int, request: Request) -> dict:
    svc = request.app.state.services
    project = await svc.projects_repo.get(pid)
    if project is None:
        raise NotFound(f"Project '{pid}' not found.")
    tasks = await svc.projects_repo.list_tasks(pid)
    convs = await svc.db.fetchall(
        "SELECT id, title, updated_at FROM conversations WHERE project_id=? "
        "ORDER BY updated_at DESC", (pid,))
    project["task_count"] = len(tasks)
    project["chat_count"] = len(convs)
    out = _serialize(project)
    out["files"] = await svc.projects_repo.list_files(pid)
    out["tasks"] = tasks
    out["conversations"] = convs
    return out


@router.patch("/{pid}")
async def update_project(pid: int, body: ProjectUpdate, request: Request) -> dict:
    svc = request.app.state.services
    if await svc.projects_repo.get(pid) is None:
        raise NotFound(f"Project '{pid}' not found.")
    await svc.projects_repo.update(pid, **body.model_dump(exclude_none=True))
    return _serialize(await svc.projects_repo.get(pid))


@router.delete("/{pid}")
async def delete_project(pid: int, request: Request) -> dict:
    svc = request.app.state.services
    if await svc.projects_repo.get(pid) is None:
        raise NotFound(f"Project '{pid}' not found.")
    await svc.projects_repo.delete(pid)
    return {"deleted": True, "id": pid}


@router.get("/{pid}/files")
async def list_files(pid: int, request: Request) -> dict:
    svc = request.app.state.services
    project = await svc.projects_repo.get(pid)
    if project is None:
        raise NotFound(f"Project '{pid}' not found.")
    ws = resolve_within(svc.settings.resolved_workspace_root,
                        project.get("root_path") or f"projects/p{pid}")
    # refresh metadata from disk (real state)
    files: list[dict] = []
    if ws.is_dir():
        for p in sorted(ws.rglob("*")):
            if not p.is_file():
                continue
            try:
                rel = p.relative_to(ws).as_posix()
            except ValueError:
                continue
            if any(part in (".git", "node_modules", ".venv", "__pycache__")
                   for part in rel.split("/")):
                continue
            try:
                size = p.stat().st_size
            except OSError:
                size = None
            files.append({"path": rel, "name": p.name, "size_bytes": size,
                          "mime": None})
    await svc.projects_repo.clear_files(pid)
    for f in files:
        await svc.projects_repo.add_file(pid, f["path"], f["name"],
                                         f["size_bytes"], f["mime"])
    return {"files": files, "workspace": str(ws)}


@router.post("/{pid}/tasks")
async def create_task(pid: int, body: ProjectTaskCreate, request: Request) -> dict:
    svc = request.app.state.services
    if await svc.projects_repo.get(pid) is None:
        raise NotFound(f"Project '{pid}' not found.")
    return await svc.projects_repo.add_task(pid, body.title, body.description)


@router.delete("/{pid}/tasks/{task_id}")
async def delete_task(pid: int, task_id: int, request: Request) -> dict:
    svc = request.app.state.services
    await svc.projects_repo.delete_task(task_id)
    return {"deleted": True, "id": task_id}
