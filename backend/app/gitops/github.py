"""GitHub REST client (P7) — thin, honest wrapper around api.github.com.

The PAT comes from the encrypted credentials store and only ever travels
in the Authorization header. No git operations here (those run through
GitService); this is account/repo metadata + repo creation only.
"""
from __future__ import annotations

import logging

import httpx

from ..core.errors import AppError

log = logging.getLogger("aicc.github")

API = "https://api.github.com"
TIMEOUT_S = 15.0


class GitHubClient:
    def __init__(self, token: str):
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "AICommandCenter",
        }

    async def _get(self, url: str, params: dict | None = None) -> httpx.Response:
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(TIMEOUT_S)) as c:
                return await c.get(f"{API}{url}", headers=self._headers,
                                   params=params or {})
        except httpx.HTTPError as exc:
            raise AppError(f"GitHub unreachable: {type(exc).__name__}",
                           code="GITHUB_UNREACHABLE", status_code=503) from exc

    def _check(self, resp: httpx.Response, what: str) -> None:
        if resp.status_code == 401:
            raise AppError("GitHub rejected the token (401). Check the PAT "
                           "under Git → GitHub → token.", code="GITHUB_AUTH",
                           status_code=403)
        if resp.status_code >= 400:
            try:
                msg = resp.json().get("message", resp.text[:200])
            except Exception:
                msg = resp.text[:200]
            raise AppError(f"GitHub {what} failed ({resp.status_code}): {msg}",
                           code="GITHUB_ERROR", status_code=502)

    async def user(self) -> dict:
        resp = await self._get("/user")
        self._check(resp, "user lookup")
        d = resp.json()
        return {"login": d.get("login"), "name": d.get("name"),
                "avatar_url": d.get("avatar_url"),
                "html_url": d.get("html_url")}

    async def repos(self, limit: int = 30) -> list[dict]:
        resp = await self._get("/user/repos", params={
            "per_page": str(max(1, min(limit, 100))), "sort": "updated",
            "affiliation": "owner"})
        self._check(resp, "repo listing")
        return [{"name": r.get("name"), "full_name": r.get("full_name"),
                 "private": r.get("private"), "html_url": r.get("html_url"),
                 "default_branch": r.get("default_branch"),
                 "clone_url": r.get("clone_url"),
                 "updated_at": r.get("updated_at")}
                for r in resp.json()]

    async def create_repo(self, name: str, *, private: bool = True,
                          description: str = "") -> dict:
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(TIMEOUT_S)) as c:
                resp = await c.post(f"{API}/user/repos", headers=self._headers,
                                    json={"name": name, "private": private,
                                          "description": description[:350]})
        except httpx.HTTPError as exc:
            raise AppError(f"GitHub unreachable: {type(exc).__name__}",
                           code="GITHUB_UNREACHABLE", status_code=503) from exc
        self._check(resp, "repo creation")
        d = resp.json()
        return {"name": d.get("name"), "full_name": d.get("full_name"),
                "private": d.get("private"), "html_url": d.get("html_url"),
                "clone_url": d.get("clone_url"),
                "default_branch": d.get("default_branch")}
