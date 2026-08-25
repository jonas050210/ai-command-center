"""Future-phase boundaries — honest HTTP 501, never fake functionality.

Agent Mode, Team Mode, Research Mode and Git/GitHub integration are
planned features (see ROADMAP). These endpoints exist so the frontend
and future clients share one stable API surface, and every call is
explicitly marked NOT IMPLEMENTED.
"""
from __future__ import annotations

from fastapi import APIRouter

from ..agent import unavailable as agent_unavailable
from ..gitops import unavailable as git_unavailable
from ..research import unavailable as research_unavailable
from ..team import unavailable as team_unavailable

router = APIRouter(tags=["future"])


@router.api_route("/agent/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def agent_boundary(path: str = "") -> None:
    agent_unavailable()


@router.api_route("/team/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def team_boundary(path: str = "") -> None:
    team_unavailable()


@router.api_route("/research/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def research_boundary(path: str = "") -> None:
    research_unavailable()


@router.api_route("/git/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def git_boundary(path: str = "") -> None:
    git_unavailable()
