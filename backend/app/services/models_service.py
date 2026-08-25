"""Model Center service — discovery, sync, testing, pull, delete.

Everything displayed comes from the provider (real data). Fields that
cannot be determined stay ``None`` → the UI renders "Unknown".
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from ..core.errors import NotFound
from ..db.repo import ModelsRepo, ProvidersRepo
from ..providers.base import ChatMessage, ChatOptions, Provider
from .model_router import classify_model

log = logging.getLogger("aicc.models")

TEST_PROMPT = "Reply with exactly: OK"


class ModelsService:
    def __init__(self, models: ModelsRepo, providers: ProvidersRepo):
        self.models = models
        self.providers = providers

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
        """Run one real inference and measure throughput (tokens/sec)."""
        row = await self.models.get(provider.name, name)
        if row is None:
            raise NotFound(f"Model '{name}' is not in the catalog. Refresh first.")
        t0 = time.monotonic()
        final = None
        cancel = asyncio.Event()
        async for chunk in provider.chat_stream(
                name, [ChatMessage(role="user", content=TEST_PROMPT)],
                ChatOptions(num_ctx=num_ctx, keep_alive=keep_alive), cancel):
            if chunk.done:
                final = chunk
        latency_ms = round((time.monotonic() - t0) * 1000, 1)
        out_tok = final.output_tokens if final else None
        in_tok = final.input_tokens if final else None
        tps = final.output_tps if final else None
        if out_tok is None and final is not None:
            out_tok = None  # runtime didn't report → stay honest
        await self.models.record_usage(provider.name, name, in_tok or 0, out_tok or 0, tps)
        return {
            "model": name, "provider": provider.name,
            "tokens_per_second": round(tps, 1) if tps else None,
            "latency_ms": latency_ms,
            "input_tokens": in_tok, "output_tokens": out_tok,
            "token_method": "exact" if (in_tok is not None and out_tok is not None) else "estimated",
            "cost_eur": 0.0,
        }
