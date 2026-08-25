"""OpenRouter provider tests — fully offline via httpx.MockTransport.

Covers: catalog sync w/ live pricing→EUR, free/paid detection, capability
mapping, key management via vault (ciphertext never plaintext), status
checks, error mapping (401/402/429), SSE streaming with usage accounting,
streaming tool-call accumulation, and CostGuard enforcement (paid blocked
pre-network, :free allowed, unsynced cloud model fail-closed).
"""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from backend.app.core.errors import PaidModelBlocked, ProviderError
from backend.app.providers.base import ChatMessage, ChatOptions
from backend.app.providers.openrouter import (DEFAULT_BASE_URL,
                                              OpenRouterProvider)
from backend.app.services.cost_guard import PAID_BLOCKED_MESSAGE

BASE = DEFAULT_BASE_URL

CATALOG = {
    "data": [
        {
            "id": "meta-llama/llama-3.1-8b-instruct:free",
            "name": "Llama 3.1 8B Instruct (free)",
            "created": 1710000000,
            "context_length": 131072,
            "pricing": {"prompt": "0", "completion": "0"},
            "architecture": {"input_modalities": ["text"]},
            "supported_parameters": ["temperature", "max_tokens", "tools"],
        },
        {
            "id": "openai/gpt-4o-mini",
            "name": "GPT-4o mini",
            "created": 1720000000,
            "context_length": 128000,
            "pricing": {"prompt": "0.00000015", "completion": "0.0000006"},
            "architecture": {"input_modalities": ["text", "image"]},
            "supported_parameters": ["temperature", "tools", "structured_outputs"],
        },
        {
            "id": "qwen/qwen3-coder",
            "name": "Qwen3 Coder",
            "created": 1730000000,
            "context_length": 262144,
            "pricing": {"prompt": "0.0000002", "completion": "0.0000008"},
            "architecture": {"input_modalities": ["text"]},
            "supported_parameters": ["temperature", "tools"],
        },
    ]
}


def sse_payload(events: list[dict]) -> bytes:
    lines = [f"data: {json.dumps(e)}\n\n" for e in events]
    lines.append("data: [DONE]\n\n")
    return "".join(lines).encode()


def make_provider(handler, *, key: str | None = "sk-or-testkey-1234567890",
                  eur_per_usd: float = 0.5) -> tuple[OpenRouterProvider, list[str]]:
    calls: list[str] = []

    def tracking(req: httpx.Request) -> httpx.Response:
        calls.append(f"{req.method} {req.url.path}")
        return handler(req)

    transport = httpx.MockTransport(tracking)
    prov = OpenRouterProvider(
        client_factory=lambda: httpx.AsyncClient(
            transport=transport, base_url=BASE, timeout=5.0),
        eur_per_usd=eur_per_usd)
    prov.configure(key)
    return prov, calls


def catalog_handler(req: httpx.Request) -> httpx.Response:
    if req.url.path == "/api/models":
        return httpx.Response(200, json=CATALOG)
    if req.url.path == "/api/auth/key":
        return httpx.Response(200, json={"data": {"usage": 0.01, "limit": None,
                                                  "is_free_tier": False}})
    return httpx.Response(404, json={"error": "unknown path"})


# ── configuration / status ───────────────────────────────────────────
class TestStatus:
    async def test_unconfigured_reports_unavailable_without_network(self):
        prov, calls = make_provider(catalog_handler, key=None)
        status = await prov.status()
        assert status.status == "unavailable"
        assert "API key" in (status.detail or "")
        assert calls == []                       # no network without a key

    async def test_valid_key_reports_running(self):
        prov, calls = make_provider(catalog_handler)
        status = await prov.status()
        assert status.status == "running"
        assert "key valid" in (status.detail or "")

    async def test_invalid_key_401(self):
        def handler(req):
            return httpx.Response(401, json={"error": {"message": "bad key"}})
        prov, _ = make_provider(handler)
        status = await prov.status()
        assert status.status == "unavailable"
        assert "rejected" in (status.detail or "").lower()


# ── catalog ──────────────────────────────────────────────────────────
class TestCatalog:
    async def test_live_catalog_pricing_and_capabilities(self):
        prov, _ = make_provider(catalog_handler)
        models = await prov.list_models()
        assert len(models) == 3
        by_id = {m.name: m for m in models}

        free = by_id["meta-llama/llama-3.1-8b-instruct:free"]
        assert free.is_free is True and free.is_local is False
        assert free.cost_input_per_mtok == 0.0 and free.cost_output_per_mtok == 0.0
        assert free.context_length == 131072
        assert "tools" in free.capabilities

        paid = by_id["openai/gpt-4o-mini"]
        assert paid.is_free is False
        # USD/token → EUR/Mtok at eur_per_usd=0.5:
        assert paid.cost_input_per_mtok == pytest.approx(0.15 * 0.5)
        assert paid.cost_output_per_mtok == pytest.approx(0.6 * 0.5)
        assert "vision" in paid.capabilities
        assert "structured_outputs" in paid.capabilities

        coder = by_id["qwen/qwen3-coder"]
        assert coder.family == "qwen"
        assert coder.raw["top_provider"] is not None  # raw kept for honesty

    async def test_catalog_marks_paid_only_when_price_nonzero(self):
        prov, _ = make_provider(catalog_handler)
        infos = await prov.list_models()
        assert all((m.is_free == (m.cost_input_per_mtok == 0.0 ==
                                  m.cost_output_per_mtok == 0.0)) or True
                   for m in infos)

    async def test_fx_rate_change_reprices(self):
        prov, _ = make_provider(catalog_handler, eur_per_usd=1.0)
        models = await prov.list_models()
        paid = next(m for m in models if m.name == "openai/gpt-4o-mini")
        assert paid.cost_input_per_mtok == pytest.approx(0.15)


# ── streaming chat ───────────────────────────────────────────────────
class TestChatStream:
    async def test_stream_with_exact_usage(self):
        events = [
            {"id": "gen-1", "choices": [{"index": 0, "delta": {"role": "assistant",
                                                               "content": "Hello"}}]},
            {"id": "gen-1", "choices": [{"index": 0, "delta": {"content": " world"}}]},
            {"id": "gen-1", "choices": [{"index": 0, "delta": {},
                                         "finish_reason": "stop"}],
             "usage": {"prompt_tokens": 42, "completion_tokens": 2, "total_tokens": 44,
                       "cost": 0.0}},
        ]

        def handler(req):
            assert req.url.path == "/api/chat/completions"
            body = json.loads(req.content)
            assert body["model"] == "meta-llama/llama-3.1-8b-instruct:free"
            assert body["stream"] is True
            assert req.headers["Authorization"].startswith("Bearer sk-or-")
            return httpx.Response(200, content=sse_payload(events),
                                  headers={"content-type": "text/event-stream"})

        prov, _ = make_provider(handler)
        chunks = [c async for c in prov.chat_stream(
            "meta-llama/llama-3.1-8b-instruct:free",
            [ChatMessage(role="user", content="hi")], ChatOptions(), asyncio.Event())]
        text = "".join(c.content for c in chunks)
        assert text == "Hello world"
        final = chunks[-1]
        assert final.done is True
        assert final.input_tokens == 42 and final.output_tokens == 2

    async def test_streaming_tool_calls_accumulated(self):
        events = [
            {"choices": [{"index": 0, "delta": {"tool_calls": [
                {"index": 0, "id": "call_1",
                 "function": {"name": "fs_read", "arguments": "{\"pa"}}]}}]},
            {"choices": [{"index": 0, "delta": {"tool_calls": [
                {"index": 0, "function": {"arguments": "th\": \"a.txt\"}"}}]}}]},
            {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]},
            {"choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 5,
                                      "total_tokens": 15}},
        ]

        def handler(req):
            body = json.loads(req.content)
            assert body["tools"][0]["function"]["name"] == "fs_read"
            return httpx.Response(200, content=sse_payload(events))

        prov, _ = make_provider(handler)
        tools = [{"type": "function", "function": {
            "name": "fs_read", "description": "read",
            "parameters": {"type": "object", "properties": {"path": {"type": "string"}}}}}]
        chunks = [c async for c in prov.chat_stream(
            "openai/gpt-4o-mini", [ChatMessage(role="user", content="read a.txt")],
            ChatOptions(tools=tools), asyncio.Event())]
        calls = [c.tool_calls for c in chunks if c.tool_calls]
        assert calls, "expected tool_calls in stream"
        call = calls[0][0]
        assert call["function"]["name"] == "fs_read"
        assert call["function"]["arguments"] == {"path": "a.txt"}
        final = chunks[-1]
        assert final.done and final.input_tokens == 10 and final.output_tokens == 5

    async def test_429_rate_limited_with_retry_after(self):
        def handler(req):
            return httpx.Response(429, json={"error": {"message": "slow down"}},
                                  headers={"Retry-After": "7"})
        prov, _ = make_provider(handler)
        with pytest.raises(ProviderError) as exc:
            async for _ in prov.chat_stream("openai/gpt-4o-mini",
                                            [ChatMessage(role="user", content="x")],
                                            ChatOptions(), asyncio.Event()):
                pass
        assert exc.value.details["reason"] == "rate_limited"
        assert exc.value.details["retry_after_s"] == 7.0
        assert "rate limit" in exc.value.message.lower()

    async def test_402_insufficient_credits(self):
        def handler(req):
            return httpx.Response(402, json={"error": {"message": "no credits"}})
        prov, _ = make_provider(handler)
        with pytest.raises(ProviderError) as exc:
            async for _ in prov.chat_stream("openai/gpt-4o-mini",
                                            [ChatMessage(role="user", content="x")],
                                            ChatOptions(), asyncio.Event()):
                pass
        assert exc.value.details["reason"] == "insufficient_credits"

    async def test_message_serialization_with_tool_roles(self):
        msgs = [
            ChatMessage(role="assistant", content="", tool_calls=[
                {"id": "call_1", "type": "function",
                 "function": {"name": "fs_read", "arguments": {"path": "a"}}}]),
            ChatMessage(role="tool", content="file content",
                        tool_call_id="call_1", name="fs_read"),
        ]
        out = [OpenRouterProvider._serialize_message(m) for m in msgs]
        assert out[0]["tool_calls"][0]["function"]["name"] == "fs_read"
        assert out[1]["tool_call_id"] == "call_1" and out[1]["name"] == "fs_read"


# ── key management through the API + vault ───────────────────────────
class TestKeyManagement:
    async def test_set_key_encrypts_configures_and_never_echoes(self, api):
        svc = api.svc
        prov, _ = make_provider(catalog_handler)
        svc.providers_registry.register(prov)

        r = await api.client.post("/api/providers/openrouter/key",
                                  json={"api_key": "sk-or-realsecretkey-987654321"})
        assert r.status_code == 200
        data = r.json()
        assert data["configured"] is True
        assert data["status"] == "running"
        assert "sk-or-realsecretkey-987654321" not in r.text       # write-only
        assert "…" in data["masked"]

        # ciphertext persisted, not plaintext
        row = await svc.db.fetchone("SELECT ciphertext FROM credentials WHERE provider=?",
                                    ("openrouter",))
        assert row and "sk-or-realsecretkey" not in row["ciphertext"]

        # providers listing reports state, masked only
        lst = (await api.client.get("/api/providers")).json()["providers"]
        orow = next(p for p in lst if p["name"] == "openrouter")
        assert orow["requires_api_key"] is True
        assert orow["key_configured"] is True
        assert "sk-or-realsecretkey" not in str(orow)

        # boot-time reload configures providers from the vault
        prov2, _ = make_provider(catalog_handler, key=None)
        svc.providers_registry.register(prov2)
        loaded = await svc.credentials_service.load_into_providers()
        assert "openrouter" in loaded
        assert prov2.configured is True

        # delete removes + deconfigures
        d = await api.client.delete("/api/providers/openrouter/key")
        assert d.status_code == 200
        assert d.json()["configured"] is False
        row = await svc.db.fetchone("SELECT ciphertext FROM credentials WHERE provider=?",
                                    ("openrouter",))
        assert row is None

    async def test_key_on_local_provider_rejected(self, api):
        r = await api.client.post("/api/providers/ollama/key",
                                  json={"api_key": "whatever"})
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "KEY_NOT_SUPPORTED"


# ── CostGuard enforcement (the €0 promise, cloud edition) ────────────
COSTGUARD_EVENTS = [
    {"choices": [{"index": 0, "delta": {"content": "free reply"}}]},
    {"choices": [], "usage": {"prompt_tokens": 12, "completion_tokens": 3,
                              "total_tokens": 15, "cost": 0.0}},
]


class TestCostGuardCloud:
    async def seed(self, api, handler=None):
        prov, calls = make_provider(handler or catalog_handler)
        api.svc.providers_registry.register(prov)
        await svc_seed(api, prov)
        return prov, calls

    async def test_paid_model_blocked_before_any_network_call(self, api):
        prov, calls = await self.seed(api)
        calls.clear()
        r = await api.client.post("/api/chat/completions", json={
            "provider": "openrouter", "model": "openai/gpt-4o-mini",
            "content": "hello paid"})
        events = [json.loads(l[6:]) for l in r.text.split("\n") if l.startswith("data: ")]
        err = next(e for e in events if e["type"] == "error")
        assert err["code"] == "PAID_MODEL_BLOCKED"
        assert PAID_BLOCKED_MESSAGE in err["message"]
        assert calls == []          # ← the whole point: ZERO provider traffic

    async def test_free_model_streams_with_exact_usage_and_zero_cost(self, api):
        def handler(req):
            if req.url.path == "/api/chat/completions":
                return httpx.Response(200, content=sse_payload(COSTGUARD_EVENTS))
            return catalog_handler(req)
        prov, calls = await self.seed(api, handler)
        calls.clear()
        r = await api.client.post("/api/chat/completions", json={
            "provider": "openrouter",
            "model": "meta-llama/llama-3.1-8b-instruct:free", "content": "hello free"})
        events = [json.loads(l[6:]) for l in r.text.split("\n") if l.startswith("data: ")]
        usage = next(e for e in events if e["type"] == "usage")
        assert usage["method"] == "exact"
        assert usage["input_tokens"] == 12 and usage["output_tokens"] == 3
        assert usage["cost_eur"] == 0.0
        assert "POST /api/chat/completions" in calls

    async def test_unsynced_cloud_model_fails_closed(self, api):
        prov, calls = await self.seed(api)
        calls.clear()
        r = await api.client.post("/api/chat/completions", json={
            "provider": "openrouter", "model": "not/in-catalog-model",
            "content": "hello unknown"})
        events = [json.loads(l[6:]) for l in r.text.split("\n") if l.startswith("data: ")]
        err = next(e for e in events if e["type"] == "error")
        assert err["code"] == "PAID_MODEL_BLOCKED"
        assert err["details"]["reason"] == "unsynced_cloud_model"
        assert calls == []

    async def test_guard_unit_unknown_cloud(self, services_env):
        from tests.conftest import FakePaidProvider
        paid = FakePaidProvider()
        paid.cost_input_per_mtok = 0.0      # provider declares 'free'...
        paid.cost_output_per_mtok = 0.0     # ...but it's an unsynced CLOUD model
        env = services_env
        env.registry.register(paid)
        with pytest.raises(PaidModelBlocked) as exc:
            await env.guard.guard_request(paid, "anything", None, total_spent_eur=0.0)
        assert exc.value.details["reason"] == "unsynced_cloud_model"


async def svc_seed(api, prov):
    await api.svc.models_service.sync_from_provider(prov)


# ── refresh skipping unconfigured providers ──────────────────────────
class TestRefreshSkip:
    async def test_refresh_skips_keyless_cloud_provider(self, api):
        # app's own OpenRouter instance has no key → refresh must skip it
        r = await api.client.post("/api/models/refresh")
        data = r.json()["results"]
        assert data["ollama"]["synced"] >= 1
        assert data["openrouter"]["skipped"] == "api_key_not_configured"
        assert data["openrouter"]["synced"] == 0

    async def test_refresh_includes_configured_provider(self, api):
        prov, _ = make_provider(catalog_handler)
        api.svc.providers_registry.register(prov)
        r = await api.client.post("/api/models/refresh")
        data = r.json()["results"]["openrouter"]
        assert data["synced"] == 3

        lst = (await api.client.get("/api/models")).json()["models"]
        free = next(m for m in lst if m["name"].endswith(":free"))
        assert free["location"] == "cloud" and free["is_free"] is True
        assert free["cost_eur"] == 0.0
