"""OpenRouter provider — the cloud gateway (OpenAI-compatible API).

Real integration against https://openrouter.ai:

* Catalog: ``GET /api/v1/models`` — live id/name/context/pricing (USD per
  token). Converted to EUR using the configured ``eur_per_usd`` rate so the
  CostGuard can enforce one currency. ``:free`` models price at exactly 0
  and therefore pass FREE_ONLY honestly.
* Chat: ``POST /api/v1/chat/completions`` with ``stream: true``; OpenRouter
  returns full usage accounting (prompt/completion tokens + credit cost) in
  the final SSE chunk — surfaced as EXACT tokens.
* Key status: ``GET /api/v1/auth/key`` — validates the key and reports
  usage/limit data. Without a key the provider reports *unavailable* with
  a clear detail message. It never silently activates.

Error mapping: 401 → invalid key, 402 → insufficient credits,
408/5xx/timeout → provider error, 429 → rate-limited (Retry-After
propagated into ``details.retry_after_s``).
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

log = logging.getLogger("aicc.openrouter")

DEFAULT_BASE_URL = "https://openrouter.ai/api"
APP_REFERER = "https://github.com/jonas050210/ai-command-center"
APP_TITLE = "AI Command Center"

ClientFactory = Callable[[], httpx.AsyncClient]


def _default_factory(base_url: str, timeout: float) -> ClientFactory:
    def make() -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=base_url, timeout=timeout)
    return make


class OpenRouterProvider(Provider):
    name = "openrouter"
    display_name = "OpenRouter (cloud)"
    is_local = False
    # Provider-level costs stay 0.0 — per-model truth lives in the synced
    # catalog rows. Unsynced cloud models are treated as UNKNOWN by the
    # CostGuard (fail-closed), never as free.
    cost_input_per_mtok = 0.0
    cost_output_per_mtok = 0.0
    requires_api_key = True

    def __init__(self, base_url: str = DEFAULT_BASE_URL, timeout: float = 300.0,
                 client_factory: ClientFactory | None = None,
                 eur_per_usd: float = 0.92):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._factory = client_factory or _default_factory(self.base_url, timeout)
        self._api_key: str | None = None
        self._eur_per_usd = eur_per_usd

    # ── configuration ────────────────────────────────────────────────
    def configure(self, api_key: str | None) -> None:
        self._api_key = api_key.strip() if api_key else None

    def set_fx_rate(self, eur_per_usd: float) -> None:
        self._eur_per_usd = eur_per_usd

    @property
    def configured(self) -> bool:
        return bool(self._api_key)

    def _headers(self) -> dict[str, str]:
        if not self._api_key:
            raise ProviderUnavailable(
                "OpenRouter is not configured — add an API key in Settings → Providers.",
                details={"provider": self.name, "reason": "missing_api_key"})
        return {
            "Authorization": f"Bearer {self._api_key}",
            "HTTP-Referer": APP_REFERER,
            "X-Title": APP_TITLE,
            "Content-Type": "application/json",
        }

    def _map_error(self, exc: Exception, action: str) -> ProviderError | ProviderUnavailable:
        if isinstance(exc, httpx.HTTPStatusError):
            status = exc.response.status_code
            if status == 401:
                return ProviderUnavailable(
                    "OpenRouter rejected the API key (401). Check the key in "
                    "Settings → Providers.", details={"reason": "invalid_api_key"})
            if status == 402:
                return ProviderError(
                    "OpenRouter reports insufficient credits (402). Add credits at "
                    "openrouter.ai or choose a :free model.",
                    details={"reason": "insufficient_credits"})
            if status == 404:
                return ProviderError(
                    f"OpenRouter could not find what you asked for while trying "
                    f"to {action}. The model id may be wrong or delisted.",
                    details={"reason": "not_found"})
            if status == 429:
                retry = exc.response.headers.get("Retry-After")
                return ProviderError(
                    "OpenRouter rate limit reached — slow down and retry. Free "
                    "models are capped (~20 requests/minute plus a daily cap).",
                    details={"reason": "rate_limited",
                             "retry_after_s": float(retry) if retry else None})
            body = ""
            try:
                body = exc.response.text[:300]
            except Exception:
                pass
            return ProviderError(f"OpenRouter error {status} while trying to {action}: "
                                 f"{body or exc.response.reason_phrase}",
                                 details={"reason": "http_error", "status": status})
        if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout)):
            return ProviderUnavailable(
                f"OpenRouter is unreachable — could not {action}. Check your "
                "internet connection.", details={"reason": "connection_failed"})
        if isinstance(exc, (httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout)):
            return ProviderUnavailable(
                f"OpenRouter did not respond in time while trying to {action}.",
                details={"reason": "timeout"})
        return ProviderError(f"OpenRouter error while trying to {action}: {exc}")

    # ── status / key health ──────────────────────────────────────────
    async def status(self) -> ProviderStatus:
        t0 = time.monotonic()
        if not self.configured:
            return ProviderStatus(
                name=self.name, status="unavailable", is_local=False,
                detail="API key not configured. Add one in Settings → Providers.")
        try:
            async with self._factory() as client:
                r = await client.get("/auth/key", headers=self._headers(), timeout=8.0)
                r.raise_for_status()
                data = (r.json() or {}).get("data") or {}
                usage, limit = data.get("usage"), data.get("limit")
                detail = f"key valid — usage {usage} credits"
                if limit is not None:
                    detail += f" / limit {limit}"
                return ProviderStatus(
                    name=self.name, status="running", is_local=False,
                    latency_ms=round((time.monotonic() - t0) * 1000, 1),
                    detail=detail)
        except ProviderUnavailable as exc:
            return ProviderStatus(name=self.name, status="unavailable",
                                  is_local=False, detail=exc.message)
        except Exception as exc:
            mapped = self._map_error(exc, "check the API key")
            return ProviderStatus(
                name=self.name,
                status="unavailable" if isinstance(mapped, ProviderUnavailable) else "error",
                is_local=False, detail=mapped.message)

    # ── catalog ──────────────────────────────────────────────────────
    async def list_models(self) -> list[ModelInfo]:
        """Live catalog from OpenRouter (public endpoint — key optional)."""
        try:
            async with self._factory() as client:
                headers: dict[str, str] = {}
                try:
                    headers = self._headers()      # send key when configured
                except ProviderUnavailable:
                    pass                           # catalog works keyless too
                r = await client.get("/models", headers=headers, timeout=30.0)
                r.raise_for_status()
                rows = (r.json() or {}).get("data") or []
                models: list[ModelInfo] = []
                for row in rows:
                    info = self._to_model_info(row)
                    if info is not None:
                        models.append(info)
                return models
        except (ProviderUnavailable, ProviderError):
            raise
        except Exception as exc:
            raise self._map_error(exc, "list the model catalog") from exc

    def _to_model_info(self, row: dict[str, Any]) -> ModelInfo | None:
        model_id = row.get("id")
        if not model_id:
            return None
        pricing = row.get("pricing") or {}

        def usd_rate(key: str) -> float:
            try:
                return float(pricing.get(key) or 0.0)
            except (TypeError, ValueError):
                return 0.0

        # OpenRouter pricing is USD per token → EUR per million tokens.
        cost_in = usd_rate("prompt") * 1_000_000 * self._eur_per_usd
        cost_out = usd_rate("completion") * 1_000_000 * self._eur_per_usd
        # Same flat rate for both directions → treat as a normal price
        is_free = usd_rate("prompt") == 0.0 and usd_rate("completion") == 0.0

        arch = row.get("architecture") or {}
        input_modalities = [str(m).lower() for m in (arch.get("input_modalities") or [])]
        supported = [str(p).lower() for p in (row.get("supported_parameters") or [])]
        capabilities: list[str] = ["completion"]
        if "tools" in supported or "tool_choice" in supported:
            capabilities.append("tools")
        if any(m not in ("text",) for m in input_modalities):
            capabilities.append("vision")
        if "structured_outputs" in supported or "response_format" in supported:
            capabilities.append("structured_outputs")
        if "reasoning" in supported:
            capabilities.append("reasoning")

        try:
            context_length = int(row["context_length"]) if row.get("context_length") else None
        except (TypeError, ValueError):
            context_length = None

        family = model_id.split("/", 1)[0] if "/" in model_id else None
        return ModelInfo(
            provider=self.name, name=model_id,
            display_name=row.get("name") or model_id,
            is_local=False, is_free=is_free,
            cost_input_per_mtok=round(cost_in, 6),
            cost_output_per_mtok=round(cost_out, 6),
            context_length=context_length,
            size_bytes=None, parameter_size=None, quantization=None,
            family=family, families=[family] if family else [],
            capabilities=capabilities,
            modified_at=row.get("created") and str(row.get("created")),
            raw={"pricing_usd": {"prompt": usd_rate("prompt"),
                                 "completion": usd_rate("completion")},
                 "top_provider": row.get("top_provider") or {},
                 "modalities": input_modalities,
                 "description": (row.get("description") or "")[:400]})

    async def show_model(self, name: str) -> dict[str, Any]:
        """Catalog rows are already fully enriched — nothing extra to fetch.
        Raises ProviderError when the model id is absent (parity with Ollama)."""
        try:
            async with self._factory() as client:
                headers = self._headers()
                r = await client.get("/models", headers=headers, timeout=15.0)
                r.raise_for_status()
                for row in (r.json() or {}).get("data") or []:
                    if row.get("id") == name:
                        return row
        except (ProviderUnavailable, ProviderError):
            raise
        except Exception as exc:
            raise self._map_error(exc, f"inspect model '{name}'") from exc
        raise ProviderError(f"Model '{name}' is not in the OpenRouter catalog.",
                            details={"reason": "model_not_found"})

    # ── chat (streaming) ─────────────────────────────────────────────
    async def chat_stream(self, model: str, messages: list[ChatMessage],
                          options: ChatOptions,
                          cancel: asyncio.Event) -> AsyncIterator[StreamChunk]:
        payload: dict[str, Any] = {
            "model": model,
            "messages": [self._serialize_message(m) for m in messages],
            "stream": True,
            "stream_options": {"include_usage": True},
            "usage": {"include": True},
        }
        if options.temperature is not None:
            payload["temperature"] = options.temperature
        if options.max_tokens:
            payload["max_tokens"] = options.max_tokens
        if options.tools:
            payload["tools"] = options.tools
        if options.format:
            payload["response_format"] = options.format

        client = self._factory()
        response: httpx.Response | None = None
        try:
            response = await client.send(
                client.build_request("POST", "/chat/completions",
                                     json=payload, headers=self._headers()),
                stream=True)
            if response.status_code >= 400:
                await response.aread()
                # re-raise through the same HTTPStatusError mapper
                raise httpx.HTTPStatusError(
                    f"HTTP {response.status_code}", request=response.request,
                    response=response)

            tool_acc = _ToolCallAccumulator()
            final_in: int | None = None
            final_out: int | None = None
            saw_done = False
            async for raw_line in response.aiter_lines():
                if cancel.is_set():
                    await response.aclose()
                    return
                line = raw_line.strip()
                if not line.startswith("data:"):
                    continue
                data_str = line[5:].strip()
                if data_str == "[DONE]":
                    saw_done = True
                    calls = tool_acc.finalize()
                    yield StreamChunk(content="", done=True,
                                      input_tokens=final_in, output_tokens=final_out,
                                      tool_calls=calls or None)
                    return
                try:
                    data = json.loads(data_str)
                except json.JSONDecodeError:
                    continue
                if data.get("error"):
                    err = data["error"]
                    raise ProviderError(
                        f"OpenRouter error: {err.get('message') or err}",
                        details={"reason": "upstream_error",
                                 "upstream_code": err.get("code")})
                usage = data.get("usage")
                if isinstance(usage, dict):
                    if usage.get("prompt_tokens") is not None:
                        final_in = int(usage["prompt_tokens"])
                    if usage.get("completion_tokens") is not None:
                        final_out = int(usage["completion_tokens"])
                for choice in data.get("choices") or []:
                    delta = choice.get("delta") or {}
                    content = delta.get("content")
                    if content:
                        yield StreamChunk(content=str(content))
                    if delta.get("tool_calls"):
                        tool_acc.feed(delta["tool_calls"])
                    if choice.get("finish_reason") == "tool_calls":
                        calls = tool_acc.finalize()
                        if calls:
                            yield StreamChunk(content="", tool_calls=calls)
            # stream ended without explicit [DONE]
            calls = tool_acc.finalize()
            yield StreamChunk(content="", done=True,
                              input_tokens=final_in, output_tokens=final_out,
                              tool_calls=calls or None)
        except (ProviderUnavailable, ProviderError):
            raise
        except Exception as exc:
            raise self._map_error(exc, "chat") from exc
        finally:
            if response is not None:
                await response.aclose()
            await client.aclose()

    @staticmethod
    def _serialize_message(m: ChatMessage) -> dict[str, Any]:
        msg: dict[str, Any] = {"role": m.role, "content": m.content}
        if m.role == "assistant" and m.tool_calls:
            msg["tool_calls"] = m.tool_calls
        if m.role == "tool":
            if m.tool_call_id:
                msg["tool_call_id"] = m.tool_call_id
            if m.name:
                msg["name"] = m.name
        return msg


# ── streaming tool-call accumulation (OpenAI delta format) ───────────
class _ToolCallAccumulator:
    """OpenAI streaming sends tool calls as indexed deltas with partial
    JSON argument strings — accumulate them into complete calls."""

    def __init__(self):
        self._slots: dict[int, dict[str, Any]] = {}

    def feed(self, deltas: list[dict[str, Any]]) -> None:
        for d in deltas:
            idx = int(d.get("index") or 0)
            slot = self._slots.setdefault(idx, {"id": None, "name": None, "args": ""})
            if d.get("id"):
                slot["id"] = d["id"]
            fn = d.get("function") or {}
            if fn.get("name"):
                slot["name"] = fn["name"]
            if fn.get("arguments"):
                slot["args"] += fn["arguments"]

    def finalize(self) -> list[dict[str, Any]]:
        calls: list[dict[str, Any]] = []
        for idx in sorted(self._slots):
            slot = self._slots[idx]
            if not slot["name"]:
                continue
            try:
                arguments = json.loads(slot["args"]) if slot["args"] else {}
                if not isinstance(arguments, dict):
                    arguments = {"value": arguments}
            except json.JSONDecodeError:
                arguments = {"_raw": slot["args"]}
            calls.append({"id": slot["id"] or f"call_{idx}",
                          "type": "function",
                          "function": {"name": slot["name"], "arguments": arguments}})
        self._slots.clear()
        return calls
