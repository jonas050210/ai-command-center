"""Git / GitHub API (P7) — repos strictly inside the workspace sandbox.

Reads (status/log/diff/branches) are always available; every mutation
(init / branch / commit / push) requires the ``git:operate`` capability
and lands in the executions audit log. The GitHub PAT is vault-stored
and only ever sent to api.github.com.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Request

from ..core.errors import AppError
from ..gitops.github import GitHubClient
from ..schemas import (GitBranchCreateRequest, GitCommitRequest,
                       GitInitRequest, GitPushRequest, GitRemoteAddRequest,
                       GithubRepoCreateRequest, GithubTokenRequest)

log = logging.getLogger("aicc.api.git")
router = APIRouter(prefix="/git", tags=["git"])


def _git(request: Request):
    return request.app.state.services.git


async def _github_client(request: Request) -> GitHubClient:
    token = await request.app.state.services.credentials_service.get_secret(
        "github")
    if not token:
        raise AppError("No GitHub token stored. Add one under "
                       "Git → GitHub → token.", code="GITHUB_NO_TOKEN",
                       status_code=403)
    return GitHubClient(token)


# ── local repo operations ────────────────────────────────────────────
@router.get("/status")
async def git_status(request: Request, path: str = ".",
                     project_id: int | None = None) -> dict:
    return await _git(request).status(path, project_id=project_id)


@router.get("/log")
async def git_log(request: Request, path: str = ".", limit: int = 20) -> dict:
    return await _git(request).log(path, limit)


@router.get("/diff")
async def git_diff(request: Request, path: str = ".", file: str | None = None,
                   staged: bool = False) -> dict:
    return await _git(request).diff(path, file, staged)


@router.get("/branches")
async def git_branches(request: Request, path: str = ".") -> dict:
    return await _git(request).branches(path)


@router.post("/init", status_code=201)
async def git_init(body: GitInitRequest, request: Request) -> dict:
    return await _git(request).init(body.path)


@router.post("/branches", status_code=201)
async def git_create_branch(body: GitBranchCreateRequest,
                            request: Request) -> dict:
    return await _git(request).create_branch(body.path, body.name)


@router.post("/commit")
async def git_commit(body: GitCommitRequest, request: Request) -> dict:
    return await _git(request).commit(body.path, body.message, body.files)


@router.post("/push")
async def git_push(body: GitPushRequest, request: Request) -> dict:
    token = await request.app.state.services.credentials_service.get_secret(
        "github")
    return await _git(request).push(body.path, body.remote, github_token=token,
                                    set_upstream=body.set_upstream)


@router.post("/remote", status_code=201)
async def git_add_remote(body: GitRemoteAddRequest, request: Request) -> dict:
    return await _git(request).add_remote(body.path, body.url, body.remote)


# ── GitHub account ───────────────────────────────────────────────────
@router.get("/github/status")
async def github_status(request: Request) -> dict:
    creds = request.app.state.services.credentials_service
    masked = await creds.masked("github")
    return {"configured": masked is not None, "masked": masked}


@router.put("/github/token")
async def github_set_token(body: GithubTokenRequest, request: Request) -> dict:
    return await request.app.state.services.credentials_service.set_secret(
        "github", body.token)


@router.delete("/github/token")
async def github_delete_token(request: Request) -> dict:
    return await request.app.state.services.credentials_service.delete_secret(
        "github")


@router.get("/github/user")
async def github_user(request: Request) -> dict:
    client = await _github_client(request)
    return await client.user()


@router.get("/github/repos")
async def github_repos(request: Request, limit: int = 30) -> dict:
    client = await _github_client(request)
    repos = await client.repos(limit)
    return {"repos": repos, "count": len(repos)}


@router.post("/github/repos", status_code=201)
async def github_create_repo(body: GithubRepoCreateRequest,
                             request: Request) -> dict:
    client = await _github_client(request)
    repo = await client.create_repo(body.name, private=body.private,
                                    description=body.description)
    return {"repo": repo}
