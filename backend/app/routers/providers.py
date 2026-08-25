"""Providers API."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Request

router = APIRouter(prefix="/providers", tags=["providers"])


@router.get("")
async def list_providers(request: Request) -> dict:
    svc = request.app.state.services
    statuses = await asyncio.gather(*(p.status() for p in svc.providers_registry.all()))
    items = []
    for p, st in zip(svc.providers_registry.all(), statuses):
        await svc.providers_repo.set_status(p.name, st.status)
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
        })
    return {"providers": items}
