"""Conversations API — history, search, rename, delete, archive, pin, favorites."""
from __future__ import annotations

from fastapi import APIRouter, Request

from ..core.errors import NotFound
from ..schemas import ConversationCreate, ConversationUpdate

router = APIRouter(prefix="/conversations", tags=["conversations"])


def serialize(conv: dict, include_messages: list[dict] | None = None) -> dict:
    out = {
        "id": conv["id"],
        "title": conv["title"],
        "model": conv["model"],
        "provider": conv["provider"],
        "system_prompt": conv["system_prompt"],
        "project_id": conv.get("project_id"),
        "pinned": bool(conv["pinned"]),
        "archived": bool(conv["archived"]),
        "favorite": bool(conv["favorite"]),
        "total_input_tokens": conv["total_input_tokens"],
        "total_output_tokens": conv["total_output_tokens"],
        "total_tokens": (conv["total_input_tokens"] or 0) + (conv["total_output_tokens"] or 0),
        "message_count": conv.get("message_count"),
        "created_at": conv["created_at"],
        "updated_at": conv["updated_at"],
    }
    if include_messages is not None:
        out["messages"] = [
            {
                "id": m["id"], "role": m["role"], "content": m["content"],
                "model": m["model"], "provider": m["provider"],
                "input_tokens": m["input_tokens"], "output_tokens": m["output_tokens"],
                "token_method": m["token_method"],     # exact | estimated
                "status": m["status"], "error": m["error"],
                "created_at": m["created_at"],
            } for m in include_messages
        ]
    return out


@router.get("")
async def list_conversations(request: Request, query: str | None = None,
                             archived: bool = False) -> dict:
    svc = request.app.state.services
    rows = await svc.conversations_repo.list(q=query, archived=archived)
    return {"conversations": [serialize(r) for r in rows], "count": len(rows)}


@router.post("")
async def create_conversation(body: ConversationCreate, request: Request) -> dict:
    svc = request.app.state.services
    conv = await svc.conversations_repo.create(body.title, body.model, body.provider,
                                               body.system_prompt,
                                               project_id=body.project_id)
    return serialize(conv)


@router.get("/{cid}")
async def get_conversation(cid: str, request: Request) -> dict:
    svc = request.app.state.services
    conv = await svc.conversations_repo.get(cid)
    if conv is None:
        raise NotFound(f"Conversation '{cid}' not found.")
    messages = await svc.messages_repo.list_for(cid)
    return serialize(conv, include_messages=messages)


@router.patch("/{cid}")
async def update_conversation(cid: str, body: ConversationUpdate, request: Request) -> dict:
    svc = request.app.state.services
    conv = await svc.conversations_repo.get(cid)
    if conv is None:
        raise NotFound(f"Conversation '{cid}' not found.")
    await svc.conversations_repo.update(cid, **body.model_dump(exclude_none=True))
    return serialize(await svc.conversations_repo.get(cid))


@router.delete("/{cid}")
async def delete_conversation(cid: str, request: Request) -> dict:
    svc = request.app.state.services
    conv = await svc.conversations_repo.get(cid)
    if conv is None:
        raise NotFound(f"Conversation '{cid}' not found.")
    await svc.conversations_repo.delete(cid)
    return {"deleted": True, "id": cid}
