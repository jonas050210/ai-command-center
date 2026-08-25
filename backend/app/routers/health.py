"""Health checks."""
from __future__ import annotations

import time

from fastapi import APIRouter, Request

from ..observability.metrics import metrics

router = APIRouter(tags=["health"])


@router.get("/health")
async def health(request: Request) -> dict:
    svc = request.app.state.services
    ollama = await svc.providers_registry.get("ollama").status()
    db_ok = await svc.db.health()
    return {
        "status": "ok" if db_ok else "degraded",
        "app": svc.settings.app_name,
        "version": svc.settings.version,
        "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "uptime_s": metrics.uptime_s(),
        "db": "ok" if db_ok else "error",
        "ollama": {
            "status": ollama.status,
            "version": ollama.version,
            "latency_ms": ollama.latency_ms,
            "models_count": ollama.models_count,
            "detail": ollama.detail,
        },
    }
