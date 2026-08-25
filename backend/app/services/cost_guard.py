"""CostGuard — the strict €0 spending protection.

HARD REQUIREMENT. Every chat request passes ``guard_request`` **before**
any provider network request is made. Enforcement happens exclusively
in this backend module; the frontend can never bypass it. There is no
automatic fallback to any paid provider, ever.
"""
from __future__ import annotations

import logging

from ..core.errors import BudgetExceeded, PaidModelBlocked
from ..observability.metrics import metrics
from ..providers.base import Provider
from .settings_service import SettingsService

log = logging.getLogger("aicc.costguard")

PAID_BLOCKED_MESSAGE = ("Paid model blocked. Free-only mode is enabled. "
                        "No money was spent.")


class CostGuard:
    def __init__(self, settings: SettingsService):
        self.settings = settings

    async def guard_request(self, provider: Provider, model: str,
                            model_row: dict | None,
                            *, total_spent_eur: float) -> None:
        """Raise BEFORE the network call if the request costs money.

        Decision inputs (in precedence order):
          1. the synced ``models`` table row (authoritative pricing), or
          2. the provider's declared per-token cost (for unsynced models).
        A model is free only when BOTH input and output rate are exactly 0.
        """
        free_only = await self.settings.get_typed("free_only")
        max_spend = await self.settings.get_typed("max_spend")

        if model_row is not None:
            cost_in = float(model_row.get("cost_input_per_mtok") or 0.0)
            cost_out = float(model_row.get("cost_output_per_mtok") or 0.0)
        else:
            cost_in = float(provider.cost_input_per_mtok)
            cost_out = float(provider.cost_output_per_mtok)
            # Unsynced CLOUD model: pricing is unknown. Fail closed — an
            # unknown model is never assumed free.
            if not provider.is_local:
                metrics.blocked_paid_requests += 1
                log.warning("BLOCKED unknown cloud model request: %s/%s "
                            "(not in synced catalog)", provider.name, model)
                raise PaidModelBlocked(
                    "Unknown cloud model blocked. This model is not in the synced "
                    "catalog, so its price is unknown — nothing was spent. "
                    "Open Model Center → Refresh and try again.",
                    details={"provider": provider.name, "model": model,
                             "reason": "unsynced_cloud_model"})
        is_free = (cost_in == 0.0 and cost_out == 0.0)

        if free_only and not is_free:
            metrics.blocked_paid_requests += 1
            log.warning("BLOCKED paid model request: %s/%s (free_only=on)",
                        provider.name, model)
            raise PaidModelBlocked(
                PAID_BLOCKED_MESSAGE,
                details={"provider": provider.name, "model": model,
                         "cost_input_per_mtok": cost_in,
                         "cost_output_per_mtok": cost_out})

        if not is_free and max_spend is not None and total_spent_eur >= max_spend:
            metrics.blocked_paid_requests += 1
            log.warning("BLOCKED request over budget: %s/%s (spent=%.4f, max=%.4f)",
                        provider.name, model, total_spent_eur, max_spend)
            raise BudgetExceeded(
                "Budget limit reached. Request blocked before spending. "
                "No money was spent.",
                details={"total_spent_eur": round(total_spent_eur, 6),
                         "max_spend_eur": max_spend})
