"""Git / GitHub API — real Git inside sandboxed project workspaces."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Request

from ..schemas import GitCommitRequest, GithubTokenRequest

log = logging.getLogger("aicc.api.git")
router = APIRouter(tags=["git"])


@router.get("/git/status")
async def git_status(request: Request, project_id: int | None = None) -> dict:
    svc = request.app.state.services
    return await svc.git.status(project_id)


@router.get("/git/log")
async def git_log(request: Request, project_id: int | None = None,
                  limit: int = 20) -> dict:
    svc = request.app.state.services
    return await svc.git.log(project_id, limit)


@router.get("/git/branches")
async def git_branches(request: Request, project_id: int | None = None) -> dict:
    svc = request.app.state.services
    return await svc.git.branches(project_id)


@router.get("/git/diff")
async def git_diff(request: Request, project_id: int | None = None,
                   cached: bool = False) -> dict:
    svc = request.app.state.services
    return await svc.git.diff(project_id, cached)


@router.post("/git/commit")
async def git_commit(body: GitCommitRequest, request: Request,
                     project_id: int | None = None) -> dict:
    svc = request.app.state.services
    return await svc.git.commit(project_id, body.message, body.paths)


# ── GitHub (real, honest unauthenticated state) ──────────────────────
@router.get("/github/state")
async def github_state(request: Request) -> dict:
    svc = request.app.state.services
    return await svc.github.state()


@router.get("/github/repositories")
async def github_repos(request: Request, limit: int = 20) -> dict:
    svc = request.app.state.services
    return await svc.github.repositories(limit)


@router.get("/github/{full_name:path}/issues")
async def github_issues(full_name: str, request: Request) -> dict:
    svc = request.app.state.services
    return await svc.github.issues(full_name)


@router.get("/github/{full_name:path}/pulls")
async def github_pulls(full_name: str, request: Request) -> dict:
    svc = request.app.state.services
    return await svc.github.pull_requests(full_name)


@router.post("/github/{full_name:path}/issues")
async def github_create_issue(full_name: str, request: Request) -> dict:
    svc = request.app.state.services
    try:
        body = await request.json()
    except Exception:
        body = {}
    title = str(body.get("title") or "").strip()[:200]
    if not title:
        from ..core.errors import BadRequest
        raise BadRequest("Issue title is required.")
    return await svc.github.create_issue(full_name, title, str(body.get("body") or ""))


@router.put("/github/credentials")
async def store_github_token(body: GithubTokenRequest, request: Request) -> dict:
    svc = request.app.state.services
    await svc.github.set_token(body.token)
    return {"stored": True, "provider": "github"}


@router.delete("/github/credentials")
async def clear_github_token(request: Request) -> dict:
    svc = request.app.state.services
    await svc.github.clear_token()
    return {"stored": False, "provider": "github"}
