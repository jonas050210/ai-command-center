"""Model Center service — discovery, sync, testing, pull, delete.

Everything displayed comes from the provider (real data). Fields that
cannot be determined stay ``None`` → the UI renders "Unknown".
"""
from __future__ import annotations

import logging
import time
from typing import Any

from ..core.errors import NotFound, BadRequest, ProviderError
from ..db.repo import ModelsRepo, ProvidersRepo
from ..providers.base import ChatMessage, Provider
from .model_router import classify_model

log = logging.getLogger("aicc.models")

TEST_PROMPT = "Reply with exactly: OK"


class ModelsService:
    def __init__(self, models: ModelsRepo, providers: ProvidersRepo, runner=None):
        self.models = models
        self.providers = providers
        self.runner = runner

    async def sync_from_provider(self, provider: Provider) -> dict:
        """Discover models from the runtime and upsert them."""
        await self.providers.upsert(provider.name, provider.display_name,
                                    provider.is_local, provider.base_url if hasattr(provider, "base_url") else None,
                                    provider.cost_input_per_mtok, provider.cost_output_per_mtok)
        infos = await provider.list_models()
        names: list[str] = []
        enriched = 0
        for info in infos:
            # contextual data from /api/show — best effort, real data only
            info = await provider.enrich(info)
            enriched += 1
            row = {
                "provider": info.provider, "name": info.name,
                "display_name": info.display_name, "is_local": info.is_local,
                "is_free": info.is_free,
                "cost_input_per_mtok": info.cost_input_per_mtok,
                "cost_output_per_mtok": info.cost_output_per_mtok,
                "context_length": info.context_length, "size_bytes": info.size_bytes,
                "parameter_size": info.parameter_size, "quantization": info.quantization,
                "family": info.family, "families": info.families,
                "capabilities": info.capabilities,
                "categories": classify_model(info.name, info.families, info.capabilities,
                                             info.parameter_size),
                "available": True, "status": "available", "raw": info.raw,
            }
            await self.models.upsert_from_provider(row)
            names.append(info.name)
        await self.models.mark_missing(provider.name, names)
        await self.providers.set_status(provider.name, "running")
        log.info("synced %d models from %s (%d enriched)", len(names), provider.name, enriched)
        return {"synced": len(names), "enriched": enriched, "models": names}

    async def test_model(self, provider: Provider, name: str,
                         num_ctx: int, keep_alive: str) -> dict[str, Any]:
        """Run one real inference and measure throughput (tokens/sec).

        Goes through ModelRunner — CostGuard runs BEFORE any provider
        network request and token/cost accounting lands in the usage
        ledger exactly like every other model call.
        """
        row = await self.models.get(provider.name, name)
        if row is None:
            raise NotFound(f"Model '{name}' is not in the catalog. Refresh first.")
        if self.runner is None:
            raise BadRequest("Model runner is not wired.", code="MODEL_RUNNER_MISSING")
        t0 = time.monotonic()
        gen = await self.runner.generate(
            messages=[ChatMessage(role="user", content=TEST_PROMPT)],
            provider_name=provider.name, model_name=name,
            num_ctx=num_ctx, keep_alive=keep_alive)
        latency_ms = round((time.monotonic() - t0) * 1000, 1)
        if gen.status == "error":
            raise ProviderError(f"Model test failed: {gen.error}",
                                details={"model": name})
        return {
            "model": name, "provider": provider.name,
            "tokens_per_second": round(gen.tokens_per_second, 1) if gen.tokens_per_second else None,
            "latency_ms": latency_ms,
            "input_tokens": gen.input_tokens, "output_tokens": gen.output_tokens,
            "token_method": gen.token_method,
            "cost_eur": gen.cost_eur,
        }
