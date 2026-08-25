"""Compare Mode (P4) — multiplexed multi-model streaming."""
from __future__ import annotations

import asyncio

from backend.app.providers.base import StreamChunk
from tests.conftest import FakeOllamaProvider, FakePaidProvider, parse_sse


async def compare(api, specs, prompt="Summarize UDP in one sentence."):
    r = await api.client.post("/api/compare/runs",
                              json={"prompt": prompt, "models": specs})
    assert r.status_code == 200, r.text
    return parse_sse(r.text)


class SlowProvider(FakeOllamaProvider):
    """Adds a delay so ordering assertions about queuing are meaningful."""

    async def chat_stream(self, model, messages, options, cancel):
        self.chat_calls += 1
        await asyncio.sleep(0.05)
        yield StreamChunk(content="slow reply")
        yield StreamChunk(content="", done=True, input_tokens=5, output_tokens=2)


class TestCompareApi:
    async def test_two_models_both_stream_to_completion(self, api):
        events = await compare(api, ["ollama/qwen3:0.6b", "ollama/deepseek-r1:7b"])
        types = [e["type"] for e in events]
        assert types[0] == "meta" and types[-1] == "done"
        meta = events[0]["comparisons"]
        assert [m["index"] for m in meta] == [0, 1]
        assert all(m["provider"] == "ollama" for m in meta)

        dones = [e for e in events if e["type"] == "model_done"]
        assert len(dones) == 2
        assert all(d["status"] == "complete" for d in dones)
        assert all(d["token_method"] == "exact" for d in dones)

        # both models' content arrived, tagged by index
        for idx in (0, 1):
            content = "".join(e["content"] for e in events
                              if e["type"] == "delta" and e["index"] == idx)
            assert "Hello from a fake local model." in content

        # usage was recorded in the shared ledger
        totals = await api.svc.usage_repo.totals()
        assert totals["events"] >= 2

        r = await api.client.get("/api/system/status")
        assert r.status_code == 200

    async def test_paid_slot_fails_while_free_complete(self, api):
        api.svc.providers_registry.register(FakePaidProvider())
        events = await compare(api, ["ollama/qwen3:0.6b", "paidtest/paid-model-1"])
        dones = {e["index"]: e for e in events if e["type"] == "model_done"}
        assert dones[0]["status"] == "complete"
        assert dones[1]["status"] == "error"
        assert dones[1]["code"] == "PAID_MODEL_BLOCKED"

    async def test_validation(self, api):
        # too few / too many / duplicates
        r = await api.client.post("/api/compare/runs",
                                  json={"prompt": "x", "models": ["only-one"]})
        assert r.status_code == 422
        r = await api.client.post(
            "/api/compare/runs",
            json={"prompt": "x", "models": ["a", "b", "c", "d", "e"]})
        assert r.status_code == 422
        events = await compare(api, ["ollama/qwen3:0.6b", "ollama/qwen3:0.6b"])
        errs = [e for e in events if e["type"] == "error"]
        assert errs and errs[0]["code"] == "COMPARE_DUPLICATE"

    async def test_unknown_provider_fails_honestly(self, api):
        events = await compare(api, ["ghost/model", "ollama/qwen3:0.6b"])
        errs = [e for e in events if e["type"] == "error"]
        assert errs and errs[0]["code"] == "PROVIDER_NOT_FOUND"

    async def test_local_provider_serializes_to_protect_vram(self, api):
        slow = SlowProvider()
        api.svc.providers_registry.register(slow)
        events = await compare(api, ["ollama/qwen3:0.6b", "ollama/deepseek-r1:7b"])
        slot_status = [(e["index"], e["status"]) for e in events
                       if e["type"] == "slot_status"]
        # the second local slot must have been queued before it ran
        assert (1, "queued") in slot_status
        assert slow.chat_calls == 2
