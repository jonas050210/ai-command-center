"""Message editing — real, persisted edits with honest history semantics.

Editing a *user* message rewrites it and truncates everything that came
after it (the conversation is re-branched from the edit point); token
totals are recomputed from the remaining messages. Editing an *assistant*
message replaces just that message's content.
"""
from __future__ import annotations

from fastapi import APIRouter, Request

from ..core.errors import BadRequest, NotFound
from ..schemas import MessageEditRequest

router = APIRouter(prefix="/messages", tags=["messages"])


def _serialize(m: dict) -> dict:
    return {
        "id": m["id"], "conversation_id": m["conversation_id"], "role": m["role"],
        "content": m["content"], "model": m["model"], "provider": m["provider"],
        "input_tokens": m["input_tokens"], "output_tokens": m["output_tokens"],
        "token_method": m["token_method"], "status": m["status"],
        "error": m["error"], "created_at": m["created_at"],
    }


@router.patch("/{mid}")
async def edit_message(mid: str, body: MessageEditRequest, request: Request) -> dict:
    svc = request.app.state.services
    msg = await svc.messages_repo.get(mid)
    if msg is None:
        raise NotFound(f"Message '{mid}' not found.")
    if msg["role"] not in ("user", "assistant"):
        raise BadRequest("Only user and assistant messages can be edited.",
                         code="MESSAGE_NOT_EDITABLE")
    content = body.content.strip()
    if not content:
        raise BadRequest("Message content must not be empty.", code="BAD_CONTENT")

    await svc.messages_repo.update_content(mid, content)
    removed = 0
    if msg["role"] == "user":
        # re-branch: drop everything after the edited message
        removed = await svc.messages_repo.delete_after(msg["conversation_id"], mid)
        await svc.conversations_repo.recount_tokens(msg["conversation_id"])
    await svc.conversations_repo.touch(msg["conversation_id"])
    return {"message": _serialize(await svc.messages_repo.get(mid)),
            "truncated_messages": removed,
            "conversation": await _conv_summary(svc, msg["conversation_id"])}


async def _conv_summary(svc, cid: str) -> dict:
    conv = await svc.conversations_repo.get(cid)
    return {
        "id": conv["id"], "total_input_tokens": conv["total_input_tokens"],
        "total_output_tokens": conv["total_output_tokens"],
        "total_tokens": (conv["total_input_tokens"] or 0)
                        + (conv["total_output_tokens"] or 0),
        "updated_at": conv["updated_at"],
    }
