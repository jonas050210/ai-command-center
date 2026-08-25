"""Cost + token tracking API.

``current`` = most recent request, ``session`` = since server start,
``total`` = persisted lifetime ledger. All EUR.
"""
from __future__ import annotations

from fastapi import APIRouter, Request

from ..observability.metrics import metrics

router = APIRouter(tags=["costs"])


@router.get("/costs")
async def costs(request: Request) -> dict:
    svc = request.app.state.services
    totals = await svc.usage_repo.totals()
    rt = await svc.settings_service.as_dict()
    last_cost = getattr(metrics, "last_request_cost_eur", 0.0)
    return {
        "currency": svc.settings.currency,
        "current": round(last_cost, 6),
        "session": round(metrics.session_cost_eur, 6),
        "total": round(totals["cost_eur"], 6),
        "free_only": rt["free_only"],
        "max_spend": rt["max_spend"],
    }


@router.get("/usage/tokens")
async def token_usage(request: Request) -> dict:
    svc = request.app.state.services
    totals = await svc.usage_repo.totals()
    per_model = await svc.usage_repo.per_model()
    return {
        "session": {"input_tokens": metrics.session_input_tokens,
                    "output_tokens": metrics.session_output_tokens,
                    "total_tokens": metrics.session_input_tokens + metrics.session_output_tokens},
        "total": {"input_tokens": totals["input_tokens"],
                  "output_tokens": totals["output_tokens"],
                  "total_tokens": totals["input_tokens"] + totals["output_tokens"]},
        "per_model": [
            {"provider": r["provider"], "model": r["model"],
             "input_tokens": r["i"], "output_tokens": r["o"],
             "total_tokens": (r["i"] or 0) + (r["o"] or 0)}
            for r in per_model
        ],
    }
