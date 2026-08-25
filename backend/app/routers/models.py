"""Model Center API — discovery, catalog, testing, pull, delete.

Only real provider data is ever returned. Undeterminable fields are
``null`` → the frontend renders "Unknown" / "Unavailable".
"""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from ..core.errors import NotFound
from ..schemas import ModelFavoriteRequest, ModelPullRequest, ModelTestRequest
from ..services.model_router import ALL_CATEGORIES

router = APIRouter(prefix="/models", tags=["models"])


def serialize(row: dict) -> dict:
    return {
        "id": row["id"],
        "provider": row["provider"],
        "name": row["name"],
        "display_name": row["display_name"],
        "is_local": bool(row["is_local"]),
        "is_free": bool(row["is_free"]),
        "location": "local" if row["is_local"] else "cloud",
        "cost_eur": round(float(row["cost_input_per_mtok"] or 0.0)
                          + float(row["cost_output_per_mtok"] or 0.0), 6),
        "context_length": row["context_length"],        # null → "Unknown"
        "size_bytes": row["size_bytes"],
        "parameter_size": row["parameter_size"],        # null → "Unknown"
        "quantization": row["quantization"],
        "family": row["family"],
        "capabilities": json.loads(row["capabilities_json"] or "[]"),
        "categories": json.loads(row["categories_json"] or "[]"),
        "available": bool(row["available"]),
        "status": row["status"],
        "favorite": bool(row["favorite"]),
        "measured_tps": row["measured_tps"],            # null → "Unknown"
        "total_input_tokens": row["total_input_tokens"],
        "total_output_tokens": row["total_output_tokens"],
        "total_tokens": (row["total_input_tokens"] or 0) + (row["total_output_tokens"] or 0),
        "usage_count": row["usage_count"],
        "last_used_at": row["last_used_at"],
        "last_seen_at": row["last_seen_at"],
    }


@router.get("")
async def list_models(request: Request, q: str | None = None,
                      category: str | None = None, favorites: bool = False,
                      sort: str = "name") -> dict:
    svc = request.app.state.services
    rows = await svc.models_repo.list(q=q, category=category, favorites=favorites, sort=sort)
    recent = await svc.models_repo.list(available_only=True, sort="recent")
    recent = [r for r in recent if r["last_used_at"]][:5]
    return {
        "models": [serialize(r) for r in rows],
        "recent": [serialize(r) for r in recent],
        "categories": ALL_CATEGORIES,
        "count": len(rows),
    }


@router.post("/refresh")
async def refresh_models(request: Request) -> dict:
    """Query Ollama live and sync the catalog (no fake data)."""
    svc = request.app.state.services
    results = {}
    for provider in svc.providers_registry.all():
        try:
            results[provider.name] = await svc.models_service.sync_from_provider(provider)
        except Exception as exc:
            await svc.providers_repo.set_status(provider.name, "unavailable")
            results[provider.name] = {
                "error": getattr(exc, "message", str(exc)),
                "synced": 0,
            }
    rt = await svc.settings_service.as_dict()
    return {"results": results, "default_model": rt["default_model"]}


@router.post("/test")
async def test_model(body: ModelTestRequest, request: Request) -> dict:
    svc = request.app.state.services
    provider = svc.providers_registry.get(body.provider)
    rt = await svc.settings_service.as_dict()
    return await svc.models_service.test_model(provider, body.name,
                                               num_ctx=rt["num_ctx"],
                                               keep_alive=rt["keep_alive"])


@router.post("/pull")
async def pull_model(body: ModelPullRequest, request: Request) -> StreamingResponse:
    """Stream real Ollama pull progress as SSE."""
    svc = request.app.state.services
    provider = svc.providers_registry.get(body.provider)
    cancel = asyncio.Event()

    async def gen():
        try:
            async for progress in provider.pull_model(body.name, cancel):
                total = progress.get("total") or 0
                completed = progress.get("completed") or 0
                pct = round((completed / total) * 100, 1) if total else None
                yield f"data: {json.dumps({'status': progress.get('status'), 'percent': pct})}\n\n"
            try:
                await svc.models_service.sync_from_provider(provider)
            except Exception:
                pass
            yield f"data: {json.dumps({'status': 'done', 'percent': 100.0})}\n\n"
        except Exception as exc:
            yield ("data: " + json.dumps({"status": "error",
                                          "message": getattr(exc, 'message', str(exc))}) + "\n\n")

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.delete("/{provider}/{name:path}")
async def delete_model(provider: str, name: str, request: Request) -> dict:
    svc = request.app.state.services
    prov = svc.providers_registry.get(provider)
    row = await svc.models_repo.get(provider, name)
    if row is None:
        raise NotFound(f"Model '{name}' not found in catalog.")
    deleted = await prov.delete_model(name)
    await svc.models_repo.delete(provider, name)
    return {"deleted": bool(deleted), "model": name}


@router.post("/{provider}/{name:path}/favorite")
async def favorite_model(provider: str, name: str, body: ModelFavoriteRequest,
                         request: Request) -> dict:
    svc = request.app.state.services
    row = await svc.models_repo.get(provider, name)
    if row is None:
        raise NotFound(f"Model '{name}' not found in catalog.")
    await svc.models_repo.set_favorite(provider, name, body.favorite)
    return {"model": name, "favorite": body.favorite}
