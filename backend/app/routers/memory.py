"""Memory & skills API (P8) — persistent memory rows + AGENT.md.

The AGENT.md file lives at the workspace root ONLY (path is fixed
server-side — no traversal possible). Everything the next agent run
will see is inspectable via GET /api/memory/context.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Request

from ..core.errors import BadRequest, NotFound
from ..memory.service import AGENT_MD_MAX, AGENT_MD_NAME
from ..schemas import AgentMdRequest, MemorySaveRequest

log = logging.getLogger("aicc.api.memory")
router = APIRouter(prefix="/memory", tags=["memory"])


def _mem(request: Request):
    return request.app.state.services.memory


@router.get("")
async def memory_list(request: Request) -> dict:
    rows = await _mem(request).list(limit=200)
    return {"memories": rows, "count": len(rows)}


@router.get("/search")
async def memory_search(request: Request, q: str = "") -> dict:
    rows = await _mem(request).search(q, limit=25)
    return {"memories": rows, "count": len(rows)}


@router.post("", status_code=201)
async def memory_save(body: MemorySaveRequest, request: Request) -> dict:
    try:
        saved = await _mem(request).save(body.key, body.content, source="user")
    except ValueError as exc:
        raise BadRequest(str(exc), code="MEMORY_INVALID") from exc
    return {"memory": saved}


@router.delete("/{mem_id}")
async def memory_delete(mem_id: int, request: Request) -> dict:
    removed = await _mem(request).delete(mem_id)
    if not removed:
        raise NotFound(f"Memory {mem_id} not found.", code="MEMORY_NOT_FOUND")
    return {"deleted": True, "id": mem_id}


# ── AGENT.md (user-authored standing instructions, workspace root) ───
@router.get("/file")
async def agent_md_get(request: Request) -> dict:
    content = _mem(request).read_agent_md(
        request.app.state.services.settings.resolved_workspace_root)
    return {"name": AGENT_MD_NAME, "content": content or "",
            "present": content is not None, "max_chars": AGENT_MD_MAX}


@router.put("/file")
async def agent_md_put(body: AgentMdRequest, request: Request) -> dict:
    svc = request.app.state.services
    root = svc.settings.resolved_workspace_root
    target = root / AGENT_MD_NAME            # fixed name, fixed root
    if not body.content.strip():
        if target.exists():
            target.unlink()                  # empty = honestly removed
        return {"name": AGENT_MD_NAME, "present": False}
    target.write_text(body.content[:AGENT_MD_MAX], encoding="utf-8")
    return {"name": AGENT_MD_NAME, "present": True,
            "chars": min(len(body.content), AGENT_MD_MAX),
            "truncated": len(body.content) > AGENT_MD_MAX}


@router.get("/context")
async def memory_context(request: Request) -> dict:
    """Exactly what the next agent run inherits: counts + previews."""
    svc = request.app.state.services
    summary = await _mem(request).context_summary(
        svc.settings.resolved_workspace_root)
    return summary
