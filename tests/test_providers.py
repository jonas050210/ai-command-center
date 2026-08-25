"""Ollama provider tests — detection, parsing, errors (mocked transport)."""
import asyncio
import json

import httpx
import pytest

from backend.app.core.errors import ProviderUnavailable, ProviderError
from backend.app.providers.base import ChatMessage, ChatOptions
from backend.app.providers.ollama import OllamaProvider
from backend.app.providers.registry import ProviderRegistry
from tests.conftest import FakeOllamaProvider


def ollama_with(handler) -> OllamaProvider:
    transport = httpx.MockTransport(handler)
    return OllamaProvider("http://testserver",
                          client_factory=lambda: httpx.AsyncClient(
                              base_url="http://testserver", transport=transport,
                              timeout=10.0))


TAGS = {"models": [
    {"name": "qwen3:0.6b", "model": "qwen3:0.6b", "size": 522_639_304,
     "digest": "abc", "modified_at": "2025-01-01T00:00:00Z",
     "details": {"format": "gguf", "family": "qwen3", "families": ["qwen3"],
                 "parameter_size": "0.6B", "quantization_level": "Q4_K_M"}},
]}


async def test_status_running():
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/version":
            return httpx.Response(200, json={"version": "0.5.1"})
        if request.url.path == "/api/tags":
            return httpx.Response(200, json=TAGS)
        return httpx.Response(404)
    status = await ollama_with(handler).status()
    assert status.status == "running"
    assert status.version == "0.5.1"
    assert status.models_count == 1
    assert status.latency_ms is not None


async def test_status_unavailable_on_connect_error():
    async def handler(request):
        raise httpx.ConnectError("connection refused", request=request)
    provider = ollama_with(handler)
    status = await provider.status()
    assert status.status == "unavailable"
    assert "unavailable" in status.detail


async def test_list_models_raises_clean_error_when_down():
    async def handler(request):
        raise httpx.ConnectError("connection refused", request=request)
    with pytest.raises(ProviderUnavailable):
        await ollama_with(handler).list_models()


async def test_list_models_parsing():
    async def handler(request):
        return httpx.Response(200, json=TAGS)
    models = await ollama_with(handler).list_models()
    assert len(models) == 1
    m = models[0]
    assert m.name == "qwen3:0.6b"
    assert m.parameter_size == "0.6B"
    assert m.quantization == "Q4_K_M"
    assert m.is_free and m.is_local
    assert m.size_bytes == 522_639_304


async def test_enrich_context_length_from_show():
    async def handler(request):
        return httpx.Response(200, json={
            "model_info": {"general.architecture": "qwen3",
                           "qwen3.context_length": 40960},
            "capabilities": ["completion", "tools"]})
    from backend.app.providers.base import ModelInfo
    info = ModelInfo(provider="ollama", name="qwen3:0.6b", display_name="qwen3:0.6b")
    info = await ollama_with(handler).enrich(info)
    assert info.context_length == 40960
    assert "tools" in info.capabilities


async def test_chat_stream_parses_ndjson_with_exact_tokens():
    lines = [
        {"model": "qwen3:0.6b", "message": {"role": "assistant", "content": "Hi "}, "done": False},
        {"model": "qwen3:0.6b", "message": {"role": "assistant", "content": "there"}, "done": False},
        {"model": "qwen3:0.6b", "message": {"role": "assistant", "content": ""}, "done": True,
         "prompt_eval_count": 12, "eval_count": 5, "eval_duration": 1_000_000_000},
    ]

    async def handler(request):
        assert request.url.path == "/api/chat"
        body = json.loads(request.content)
        assert body["model"] == "qwen3:0.6b"
        assert body["stream"] is True
        assert body["options"]["num_ctx"] == 2048
        payload = "".join(json.dumps(l) + "\n" for l in lines).encode()
        return httpx.Response(200, content=payload)

    provider = ollama_with(handler)
    chunks = []
    async for chunk in provider.chat_stream(
            "qwen3:0.6b", [ChatMessage(role="user", content="hello")],
            ChatOptions(num_ctx=2048), asyncio.Event()):
        chunks.append(chunk)
    contents = "".join(c.content for c in chunks if not c.done)
    assert contents == "Hi there"
    final = chunks[-1]
    assert final.done and final.input_tokens == 12 and final.output_tokens == 5
    assert final.output_tps == pytest.approx(5.0)


async def test_chat_stream_cancelled_stops_early():
    lines = [{"message": {"role": "assistant", "content": f"w{i} "}, "done": False}
             for i in range(10)]
    lines.append({"message": {"role": "assistant", "content": ""}, "done": True,
                  "prompt_eval_count": 1, "eval_count": 10})

    async def handler(request):
        return httpx.Response(200, content="".join(json.dumps(l) + "\n" for l in lines).encode())

    provider = ollama_with(handler)
    cancel = asyncio.Event()
    got = []

    async def consume():
        async for chunk in provider.chat_stream(
                "qwen3:0.6b", [ChatMessage(role="user", content="x")],
                ChatOptions(), cancel):
            got.append(chunk.content)
            if len(got) == 2:
                cancel.set()

    await consume()
    assert "".join(got) == "w0 w1 "


async def test_chat_stream_model_not_found():
    async def handler(request):
        return httpx.Response(404, json={"error": "model 'nope' not found"})
    provider = ollama_with(handler)
    with pytest.raises(ProviderError) as exc:
        async for _ in provider.chat_stream("nope", [ChatMessage("user", "x")],
                                            ChatOptions(), asyncio.Event()):
            pass
    assert exc.value.details.get("reason") == "model_not_found"


async def test_delete_model():
    async def handler(request):
        assert request.method == "DELETE"
        return httpx.Response(200)
    assert await ollama_with(handler).delete_model("qwen3:0.6b") is True

    async def handler404(request):
        return httpx.Response(404)
    assert await ollama_with(handler404).delete_model("missing") is False


async def test_pull_stream_progress():
    lines = [{"status": "pulling manifest"},
             {"status": "downloading", "total": 100, "completed": 50},
             {"status": "success"}]

    async def handler(request):
        return httpx.Response(200, content="".join(json.dumps(l) + "\n" for l in lines).encode())

    provider = ollama_with(handler)
    events = [e async for e in provider.pull_model("qwen3:0.6b", asyncio.Event())]
    assert events[0]["status"] == "pulling manifest"
    assert events[-1]["status"] == "success"


def test_registry():
    reg = ProviderRegistry()
    reg.register(FakeOllamaProvider())
    assert reg.names() == ["ollama"]
    assert reg.get("ollama").name == "ollama"
    from backend.app.core.errors import BadRequest
    with pytest.raises(BadRequest):
        reg.get("nope")
