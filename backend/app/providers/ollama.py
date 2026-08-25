"""Ollama provider — the primary local AI runtime.

Talks to the Ollama HTTP API (default http://localhost:11434) with
httpx. All network failures are mapped to clean ProviderUnavailable /
ProviderError exceptions; detection distinguishes *running*,
*unavailable* (connection refused) and *error* states.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, AsyncIterator, Callable

import httpx

from ..core.errors import ProviderError, ProviderUnavailable
from .base import (ChatMessage, ChatOptions, ModelInfo, Provider, ProviderStatus,
                   StreamChunk)

log = logging.getLogger("aicc.ollama")

ClientFactory = Callable[[], httpx.AsyncClient]


def _default_factory(base_url: str, timeout: float) -> ClientFactory:
    def make() -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=base_url, timeout=timeout)
    return make


class OllamaProvider(Provider):
    name = "ollama"
    display_name = "Ollama (local)"
    is_local = True
    cost_input_per_mtok = 0.0     # local inference is free
    cost_output_per_mtok = 0.0

    def __init__(self, base_url: str, timeout: float = 300.0,
                 client_factory: ClientFactory | None = None):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._factory = client_factory or _default_factory(self.base_url, timeout)

    # ── helpers ──────────────────────────────────────────────────────
    def _map_error(self, exc: Exception, action: str) -> ProviderUnavailable | ProviderError:
        if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout)):
            return ProviderUnavailable(
                f"Ollama is unavailable — could not {action} at {self.base_url}. "
                "Start Ollama (e.g. `ollama serve`) and retry.",
                details={"host": self.base_url, "reason": "connection_failed"})
        if isinstance(exc, (httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout)):
            return ProviderUnavailable(
                f"Ollama did not respond in time while trying to {action}.",
                details={"host": self.base_url, "reason": "timeout"})
        return ProviderError(f"Ollama error while trying to {action}: {exc}",
                             details={"host": self.base_url})

    # ── detection / health ───────────────────────────────────────────
    async def status(self) -> ProviderStatus:
        t0 = time.monotonic()
        try:
            async with self._factory() as client:
                r = await client.get("/api/version", timeout=5.0)
                r.raise_for_status()
                version = r.json().get("version")
                models_count: int | None = None
                try:
                    tags = await client.get("/api/tags", timeout=5.0)
                    models_count = len(tags.json().get("models", []))
                except Exception:
                    models_count = None
                return ProviderStatus(
                    name=self.name, status="running", is_local=True, version=version,
                    latency_ms=round((time.monotonic() - t0) * 1000, 1),
                    models_count=models_count)
        except Exception as exc:
            mapped = self._map_error(exc, "connect")
            return ProviderStatus(
                name=self.name, status="unavailable", is_local=True,
                latency_ms=round((time.monotonic() - t0) * 1000, 1),
                detail=mapped.message)

    # ── models ───────────────────────────────────────────────────────
    async def list_models(self) -> list[ModelInfo]:
        try:
            async with self._factory() as client:
                r = await client.get("/api/tags", timeout=15.0)
                r.raise_for_status()
                models: list[ModelInfo] = []
                for m in r.json().get("models", []):
                    details = m.get("details") or {}
                    name = m.get("name") or m.get("model") or ""
                    if not name:
                        continue
                    models.append(ModelInfo(
                        provider=self.name, name=name,
                        display_name=name.split("/")[-1],
                        is_local=True, is_free=True,
                        size_bytes=m.get("size"),
                        parameter_size=details.get("parameter_size"),
                        quantization=details.get("quantization_level"),
                        family=details.get("family"),
                        families=list(details.get("families") or []),
                        modified_at=m.get("modified_at"),
                        raw={"digest": m.get("digest")}))
                return models
        except (ProviderUnavailable, ProviderError):
            raise
        except Exception as exc:
            raise self._map_error(exc, "list installed models") from exc

    async def show_model(self, name: str) -> dict[str, Any]:
        try:
            async with self._factory() as client:
                r = await client.post("/api/show", json={"model": name}, timeout=15.0)
                r.raise_for_status()
                return r.json()
        except (ProviderUnavailable, ProviderError):
            raise
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                raise ProviderError(f"Model '{name}' is not installed in Ollama.",
                                    details={"reason": "model_not_found"}) from exc
            raise self._map_error(exc, f"inspect model '{name}'") from exc
        except Exception as exc:
            raise self._map_error(exc, f"inspect model '{name}'") from exc

    async def enrich(self, info: ModelInfo) -> ModelInfo:
        """Fill context length and capabilities from /api/show (real data)."""
        try:
            show = await self.show_model(info.name)
        except Exception as exc:
            log.debug("enrich failed for %s: %s", info.name, exc)
            return info
        model_info = show.get("model_info") or {}
        for key, value in model_info.items():
            if key.endswith(".context_length") and isinstance(value, int):
                info.context_length = value
                break
        caps = show.get("capabilities")
        if isinstance(caps, list):
            info.capabilities = [str(c) for c in caps]
        info.raw = {**info.raw, "show": {
            k: show.get(k) for k in ("parameters", "template") if show.get(k)}}
        return info

    # ── chat (streaming) ─────────────────────────────────────────────
    async def chat_stream(self, model: str, messages: list[ChatMessage],
                          options: ChatOptions,
                          cancel: asyncio.Event) -> AsyncIterator[StreamChunk]:
        payload: dict[str, Any] = {
            "model": model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "stream": True,
        }
        opts: dict[str, Any] = {}
        if options.num_ctx:
            opts["num_ctx"] = options.num_ctx
        if options.temperature is not None:
            opts["temperature"] = options.temperature
        if opts:
            payload["options"] = opts
        if options.keep_alive:
            payload["keep_alive"] = options.keep_alive

        client = self._factory()
        response: httpx.Response | None = None
        try:
            response = await client.send(
                client.build_request("POST", "/api/chat", json=payload), stream=True)
            if response.status_code == 404:
                await response.aread()
                raise ProviderError(
                    f"Model '{model}' is not installed in Ollama. Pull it from the "
                    "Model Center first.", details={"reason": "model_not_found"})
            if response.status_code >= 400:
                body = (await response.aread()).decode(errors="replace")[:500]
                raise ProviderError(f"Ollama chat failed ({response.status_code}): {body}")
            async for line in response.aiter_lines():
                if cancel.is_set():
                    await response.aclose()
                    return
                if not line.strip():
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if data.get("error"):
                    raise ProviderError(f"Ollama error: {data['error']}")
                msg = data.get("message") or {}
                done = bool(data.get("done"))
                yield StreamChunk(
                    content=msg.get("content", "") or "",
                    done=done,
                    input_tokens=data.get("prompt_eval_count") if done else None,
                    output_tokens=data.get("eval_count") if done else None,
                    eval_duration_ns=data.get("eval_duration") if done else None,
                )
                if done:
                    return
        except (ProviderUnavailable, ProviderError):
            raise
        except Exception as exc:
            raise self._map_error(exc, "chat") from exc
        finally:
            if response is not None:
                await response.aclose()
            await client.aclose()

    # ── management (safe pull/delete) ────────────────────────────────
    async def pull_model(self, name: str,
                         cancel: asyncio.Event) -> AsyncIterator[dict[str, Any]]:
        client = self._factory()
        response: httpx.Response | None = None
        try:
            response = await client.send(
                client.build_request("POST", "/api/pull",
                                     json={"model": name, "stream": True}),
                stream=True)
            if response.status_code >= 400:
                body = (await response.aread()).decode(errors="replace")[:500]
                raise ProviderError(f"Ollama pull failed ({response.status_code}): {body}")
            async for line in response.aiter_lines():
                if cancel.is_set():
                    await response.aclose()
                    yield {"status": "cancelled"}
                    return
                if not line.strip():
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue
        except (ProviderUnavailable, ProviderError):
            raise
        except Exception as exc:
            raise self._map_error(exc, f"pull model '{name}'") from exc
        finally:
            if response is not None:
                await response.aclose()
            await client.aclose()

    async def delete_model(self, name: str) -> bool:
        try:
            async with self._factory() as client:
                r = await client.request("DELETE", "/api/delete",
                                         json={"model": name}, timeout=60.0)
                if r.status_code == 404:
                    return False
                r.raise_for_status()
                return True
        except (ProviderUnavailable, ProviderError):
            raise
        except httpx.HTTPStatusError:
            return False
        except Exception as exc:
            raise self._map_error(exc, f"delete model '{name}'") from exc
