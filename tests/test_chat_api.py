"""Chat API tests — streaming, tokens, persistence, blocking, stop, regenerate."""
import asyncio

from backend.app.services.cost_guard import PAID_BLOCKED_MESSAGE
from tests.conftest import FakePaidProvider, parse_sse


async def seed_models(env):
    await env.client.post("/api/models/refresh")


async def send_chat(env, **overrides):
    body = {"content": "Hello there", **overrides}
    r = await env.client.post("/api/chat/completions", json=body)
    assert r.status_code == 200, r.text
    return parse_sse(r.text)


class TestChatFlow:
    async def test_full_stream_flow_with_exact_tokens(self, api):
        await seed_models(api)
        events = await send_chat(api)
        types = [e["type"] for e in events]
        assert types[0] == "meta"
        assert "delta" in types
        assert types[-2] == "usage"
        assert types[-1] == "done"

        meta = events[0]
        assert meta["model"] == api.settings.default_model  # not hardcoded
        assert meta["provider"] == "ollama"

        content = "".join(e["content"] for e in events if e["type"] == "delta")
        assert "fake local model" in content

        usage = next(e for e in events if e["type"] == "usage")
        assert usage["method"] == "exact"          # from fake provider counts
        assert usage["input_tokens"] > 0
        assert usage["output_tokens"] > 0
        assert usage["cost_eur"] == 0.0

        # persisted with exact accounting
        conv = (await api.client.get(
            f"/api/conversations/{meta['conversation_id']}")).json()
        assistant = [m for m in conv["messages"] if m["role"] == "assistant"][0]
        assert assistant["token_method"] == "exact"
        assert assistant["status"] == "complete"
        assert assistant["content"] == content
        assert conv["total_tokens"] > 0

    async def test_conversation_created_with_message(self, api):
        await seed_models(api)
        events = await send_chat(api, content="A question about spaceships")
        conv_id = events[0]["conversation_id"]
        conv = (await api.client.get(f"/api/conversations/{conv_id}")).json()
        assert conv["id"] == conv_id
        roles = [m["role"] for m in conv["messages"]]
        assert roles == ["user", "assistant"]

    async def test_existing_conversation_appends(self, api):
        await seed_models(api)
        first = await send_chat(api, content="First")
        conv_id = first[0]["conversation_id"]
        second = await send_chat(api, conversation_id=conv_id, content="Second")
        assert second[0]["conversation_id"] == conv_id
        conv = (await api.client.get(f"/api/conversations/{conv_id}")).json()
        assert len([m for m in conv["messages"] if m["role"] == "user"]) == 2

    async def test_model_selection_per_request(self, api):
        await seed_models(api)
        events = await send_chat(api, model="deepseek-r1:7b")
        assert events[0]["model"] == "deepseek-r1:7b"

    async def test_costs_endpoint_tracks_zero_euros(self, api):
        await seed_models(api)
        await send_chat(api)
        costs = (await api.client.get("/api/costs")).json()
        assert costs["currency"] == "EUR"
        assert costs["current"] == 0.0
        assert costs["session"] == 0.0
        assert costs["total"] == 0.0
        tokens = (await api.client.get("/api/usage/tokens")).json()
        assert tokens["total"]["total_tokens"] > 0
        assert tokens["total"]["input_tokens"] > 0

    async def test_empty_message_rejected(self, api):
        r = await api.client.post("/api/chat/completions", json={"content": "   "})
        # validation happens either in pydantic or in service
        events = parse_sse(r.text)
        assert r.status_code == 200 and events[-1]["type"] == "error"


class TestRegenerateAndStop:
    async def test_regenerate_replaces_assistant_message(self, api):
        await seed_models(api)
        events = await send_chat(api)
        meta = events[0]
        r = await api.client.post("/api/chat/regenerate",
                                  json={"message_id": meta["assistant_message_id"]})
        assert r.status_code == 200
        regen = parse_sse(r.text)
        assert regen[0]["type"] == "meta"
        assert regen[0]["assistant_message_id"] == meta["assistant_message_id"]
        assert regen[-1]["type"] == "done"
        conv = (await api.client.get(
            f"/api/conversations/{meta['conversation_id']}")).json()
        assistants = [m for m in conv["messages"] if m["role"] == "assistant"]
        assert len(assistants) == 1  # replaced, not duplicated

    async def test_regenerate_rejects_user_message(self, api):
        await seed_models(api)
        events = await send_chat(api)
        meta = events[0]
        r = await api.client.post("/api/chat/regenerate",
                                  json={"message_id": meta["user_message_id"]})
        assert parse_sse(r.text)[-1]["type"] == "error"

    async def test_stop_unknown_request(self, api):
        r = await api.client.post("/api/chat/stop", json={"request_id": "nope"})
        assert r.status_code == 404
        assert r.json()["error"]["code"] == "REQUEST_NOT_FOUND"

    async def test_stop_cancels_active_stream(self, api):
        """Stop a real in-flight stream via the request registry."""
        manager = api.svc.chat.requests
        cancel = manager.start("req-1")
        assert manager.stop("req-1") is True
        assert cancel.is_set()
        # provider honours the cancel flag
        chunks = []
        from backend.app.providers.base import ChatMessage, ChatOptions
        async for chunk in api.ollama.chat_stream(
                "qwen3:0.6b", [ChatMessage("user", "x")], ChatOptions(), cancel):
            chunks.append(chunk)
        assert chunks == []  # stopped immediately


class TestPaidModelBlocking:
    async def test_paid_model_blocked_before_network(self, api):
        """HARD REQUIREMENT: paid blocked, exact message, zero spend."""
        paid = FakePaidProvider()
        api.svc.providers_registry.register(paid)
        # sync paid model into the catalog (real pricing in DB row)
        await api.svc.models_service.sync_from_provider(paid)

        r = await api.client.post("/api/chat/completions", json={
            "content": "hello", "provider": "paidtest", "model": "paid-model-1"})
        events = parse_sse(r.text)
        error = events[-1]
        assert error["type"] == "error"
        assert error["code"] == "PAID_MODEL_BLOCKED"
        assert error["status_code"] == 403
        assert error["message"] == PAID_BLOCKED_MESSAGE

        # no provider traffic happened
        assert paid.chat_calls == 0
        # nothing recorded in the ledger
        usage = (await api.client.get("/api/usage/tokens")).json()
        assert usage["total"]["total_tokens"] == 0
        costs = (await api.client.get("/api/costs")).json()
        assert costs["total"] == 0.0

    async def test_free_only_cannot_be_bypassed_by_request_body(self, api):
        """Frontend/request params cannot disable the guard."""
        paid = FakePaidProvider()
        api.svc.providers_registry.register(paid)
        await api.svc.models_service.sync_from_provider(paid)
        for sneaky in ({"free_only": False}, {"max_spend": 99}, {"bypass": True}):
            body = {"content": "hi", "provider": "paidtest",
                    "model": "paid-model-1", **sneaky}
            r = await api.client.post("/api/chat/completions", json=body)
            assert parse_sse(r.text)[-1]["code"] == "PAID_MODEL_BLOCKED"
        assert paid.chat_calls == 0


class TestOllamaUnavailable:
    async def test_chat_surfaces_clean_error_when_ollama_down(self, api):
        api.ollama.running = False

        async def fail_stream(*a, **k):
            from backend.app.core.errors import ProviderUnavailable
            raise ProviderUnavailable("Ollama is unavailable — start it with `ollama serve`.")
            yield  # pragma: no cover

        api.ollama.chat_stream = fail_stream
        events = await send_chat(api)
        assert events[-1]["type"] == "error"
        assert "unavailable" in events[-1]["message"].lower()
        # assistant message persisted as error state
        conv = (await api.client.get(
            f"/api/conversations/{events[0]['conversation_id']}")).json()
        assistant = [m for m in conv["messages"] if m["role"] == "assistant"][0]
        assert assistant["status"] == "error"
