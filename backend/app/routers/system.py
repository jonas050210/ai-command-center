"""System status — powers the right-hand Status panel."""
from __future__ import annotations

from fastapi import APIRouter, Request

from ..observability.metrics import metrics

router = APIRouter(prefix="/system", tags=["system"])


@router.get("/security/state")
async def security_state(request: Request) -> dict:
    """Non-sensitive security posture (never reveals the token itself)."""
    svc = request.app.state.services
    s = svc.settings
    return {
        "loopback_binding": s.binds_loopback,
        "token_required_off_loopback": not s.binds_loopback,
        "rate_limits_enabled": s.enable_rate_limits,
        "allowed_hostnames": sorted(s.allowed_hostnames()),
    }


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
        # Lightweight provider overview (no live network probes on the 10s
        # polling path — /api/providers and /api/health do live checks).
        "providers": [
            {
                "name": p.name,
                "display_name": p.display_name,
                "is_local": p.is_local,
                "configured": (p.name == "ollama")
                    or await svc.credentials_service.has_key(p.name),
                "last_status": (await svc.providers_repo.get(p.name) or {}).get("status"),
            } for p in svc.providers_registry.all()
        ],
        "metrics": {
            "uptime_s": metrics.uptime_s(),
            "http_requests": metrics.http_requests,
            "chat_requests": metrics.chat_requests,
            "chat_errors": metrics.chat_errors,
            "blocked_paid_requests": metrics.blocked_paid_requests,
        },
        "server_time": __import__("time").strftime("%Y-%m-%dT%H:%M:%SZ", __import__("time").gmtime()),
    }
