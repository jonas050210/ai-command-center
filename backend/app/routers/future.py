"""Future-phase boundaries — honest HTTP 501, never fake functionality.

Git/GitHub integration is a planned feature (see ROADMAP). Agent,
Projects, Compare, Team and Research Mode are real. These endpoints
exist so clients share one stable API surface, and every call to an
unshipped feature is explicitly marked NOT IMPLEMENTED.
"""
from __future__ import annotations

from fastapi import APIRouter

from ..gitops import unavailable as git_unavailable

router = APIRouter(tags=["future"])


@router.api_route("/git/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def git_boundary(path: str = "") -> None:
    git_unavailable()
