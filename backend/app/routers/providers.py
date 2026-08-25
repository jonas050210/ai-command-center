"""Providers API — status listing + API-key management.

Keys are write-only: they can be saved or deleted, but are NEVER returned
by any endpoint — only a masked hint (``sk-or-12…xyzw``). Ciphertext lives
in the ``credentials`` table; encryption is the vault's job.
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Request

from ..schemas import ProviderKeyRequest

router = APIRouter(prefix="/providers", tags=["providers"])


@router.get("")
async def list_providers(request: Request) -> dict:
    svc = request.app.state.services
    statuses = await asyncio.gather(*(p.status() for p in svc.providers_registry.all()))
    items = []
    for p, st in zip(svc.providers_registry.all(), statuses):
        await svc.providers_repo.set_status(p.name, st.status)
        key_configured = await svc.credentials_service.has_key(p.name)
        items.append({
            "name": p.name,
            "display_name": p.display_name,
            "is_local": p.is_local,
            "status": st.status,
            "version": st.version,
            "latency_ms": st.latency_ms,
            "models_count": st.models_count,
            "detail": st.detail,
            "base_url": getattr(p, "base_url", None),
            "cost_input_per_mtok": p.cost_input_per_mtok,
            "cost_output_per_mtok": p.cost_output_per_mtok,
            "is_free": p.cost_input_per_mtok == 0.0 and p.cost_output_per_mtok == 0.0,
            "supports_pull": p.supports_pull,
            "supports_delete": p.supports_delete,
            "requires_api_key": p.requires_api_key,
            "key_configured": key_configured,
            "key_masked": await svc.credentials_service.masked(p.name) if key_configured else None,
        })
    return {"providers": items}


@router.post("/{name}/key")
async def set_provider_key(name: str, body: ProviderKeyRequest, request: Request) -> dict:
    svc = request.app.state.services
    provider = svc.providers_registry.get(name)
    if not provider.requires_api_key:
        from ..core.errors import BadRequest
        raise BadRequest(f"Provider '{name}' does not use API keys.",
                         code="KEY_NOT_SUPPORTED")
    result = await svc.credentials_service.set_key(name, body.api_key)
    # Validate immediately + sync the catalog so the Model Center shows
    # real cloud data right away. Failures surface as detail, never fake OK.
    status = await provider.status()
    result["status"] = status.status
    result["detail"] = status.detail
    if status.status == "running":
        try:
            sync = await svc.models_service.sync_from_provider(provider)
            result["synced_models"] = sync["synced"]
        except Exception as exc:  # never undo a valid key save
            result["sync_error"] = getattr(exc, "message", str(exc))
    return result


@router.delete("/{name}/key")
async def delete_provider_key(name: str, request: Request) -> dict:
    svc = request.app.state.services
    svc.providers_registry.get(name)          # 400 for unknown provider names
    result = await svc.credentials_service.delete_key(name)
    provider = svc.providers_registry.get(name)
    result["status"] = (await provider.status()).status
    return result
