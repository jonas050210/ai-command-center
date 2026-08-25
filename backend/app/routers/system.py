"""System status — powers the right-hand Status panel."""
from __future__ import annotations

from fastapi import APIRouter, Request

from ..observability.metrics import metrics

router = APIRouter(prefix="/system", tags=["system"])


@router.get("/status")
async def system_status(request: Request) -> dict:
    svc = request.app.state.services
    rt = await svc.settings_service.as_dict()
    provider = svc.providers_registry.get("ollama")
    status = await provider.status()
    models_count = len(await svc.models_repo.list(available_only=True))
    return {
        "ollama": {
            "status": status.status,
            "version": status.version,
            "latency_ms": status.latency_ms,
            "models_count": status.models_count,
            "detail": status.detail,
            "host": getattr(provider, "base_url", None),
        },
        "models_in_catalog": models_count,
        "runtime": rt,
        "currency": svc.settings.currency,
        "metrics": {
            "uptime_s": metrics.uptime_s(),
            "http_requests": metrics.http_requests,
            "chat_requests": metrics.chat_requests,
            "chat_errors": metrics.chat_errors,
            "blocked_paid_requests": metrics.blocked_paid_requests,
        },
        "server_time": __import__("time").strftime("%Y-%m-%dT%H:%M:%SZ", __import__("time").gmtime()),
    }
