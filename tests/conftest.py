"""Shared fixtures: temp settings, db, fake providers, API client."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any, AsyncIterator

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.config import DEFAULT_MODEL_NAME, Settings          # noqa: E402
from backend.app.db.database import Database                          # noqa: E402
from backend.app.db.migrations import migrate                         # noqa: E402
from backend.app.main import create_app                               # noqa: E402
from backend.app.providers.base import (ChatMessage, ChatOptions,     # noqa: E402
                                        ModelInfo, Provider, ProviderStatus,
                                        StreamChunk)
from backend.app.providers.registry import ProviderRegistry           # noqa: E402
from backend.app.services.cost_guard import CostGuard                 # noqa: E402
from backend.app.services.model_router import ModelRouter             # noqa: E402
from backend.app.services.settings_service import SettingsService     # noqa: E402
from backend.app.db.repo import ExecutionsRepo, ModelsRepo, SettingsRepo  # noqa: E402
from backend.app.tools.builtin import register_builtin_tools          # noqa: E402
from backend.app.tools.executor import ToolExecutor                   # noqa: E402
from backend.app.tools.registry import ToolContext, ToolRegistry      # noqa: E402


# ── settings / db ────────────────────────────────────────────────────
@pytest.fixture
def test_settings(tmp_path) -> Settings:
    return Settings(data_dir=tmp_path / "data", default_model=DEFAULT_MODEL_NAME,
                    free_only=True, max_spend=0.0, ollama_host="http://testserver")


@pytest.fixture
async def db(test_settings) -> Database:
    database = Database(test_settings.db_path)
    await database.connect()
    await migrate(database)
    yield database
    await database.close()


# ── fake providers (never touch the network) ─────────────────────────
class FakeOllamaProvider(Provider):
    """In-process stand-in with realistic Ollama-style responses."""

    name = "ollama"
    display_name = "Ollama (fake)"
    is_local = True
    cost_input_per_mtok = 0.0
    cost_output_per_mtok = 0.0
    supports_pull = True
    supports_delete = True

    def __init__(self, reply: str = "Hello from a fake local model.",
                 running: bool = True):
        self.reply = reply
        self.running = running
        self.chat_calls = 0
        self.deleted: list[str] = []
        self.last_options: ChatOptions | None = None
        self.last_tools: list | None = None

    async def status(self) -> ProviderStatus:
        if not self.running:
            return ProviderStatus(name=self.name, status="unavailable",
                                  detail="fake offline")
        return ProviderStatus(name=self.name, status="running", version="0.0-test",
                              latency_ms=1.0, models_count=2)

    async def list_models(self) -> list[ModelInfo]:
        return [
            ModelInfo(provider=self.name, name="qwen3:0.6b", display_name="qwen3:0.6b",
                      context_length=40960, size_bytes=522_000_000,
                      parameter_size="0.6B", quantization="Q4_K_M", family="qwen3",
                      families=["qwen3"], capabilities=["completion", "tools"]),
            ModelInfo(provider=self.name, name="deepseek-r1:7b", display_name="deepseek-r1:7b",
                      context_length=131072, size_bytes=4_700_000_000,
                      parameter_size="7B", quantization="Q4_K_M", family="qwen2",
                      families=["qwen2"], capabilities=["completion"]),
        ]

    async def show_model(self, name: str) -> dict[str, Any]:
        return {"model_info": {"qwen3.context_length": 40960},
                "capabilities": ["completion", "tools"]}

    async def enrich(self, info: ModelInfo) -> ModelInfo:
        if info.context_length is None:
            info.context_length = 40960
        return info

    async def chat_stream(self, model: str, messages: list[ChatMessage],
                          options: ChatOptions,
                          cancel: asyncio.Event) -> AsyncIterator[StreamChunk]:
        self.chat_calls += 1
        self.last_options = options
        prompt_tokens = sum(max(1, len(m.content) // 4) + 4 for m in messages)
        for word in self.reply.split(" "):
            if cancel.is_set():
                return
            yield StreamChunk(content=word + " ")
            await asyncio.sleep(0)
        yield StreamChunk(content="", done=True, input_tokens=prompt_tokens,
                          output_tokens=len(self.reply.split()),
                          eval_duration_ns=500_000_000)

    async def delete_model(self, name: str) -> bool:
        self.deleted.append(name)
        return True


class FakePaidProvider(Provider):
    """Pretend cloud provider with real non-zero prices (for CostGuard tests)."""

    name = "paidtest"
    display_name = "Paid Test Provider"
    is_local = False
    cost_input_per_mtok = 5.0
    cost_output_per_mtok = 15.0

    def __init__(self):
        self.chat_calls = 0

    async def status(self) -> ProviderStatus:
        return ProviderStatus(name=self.name, status="running", is_local=False)

    async def list_models(self) -> list[ModelInfo]:
        return [ModelInfo(provider=self.name, name="paid-model-1",
                          display_name="Paid Model 1", is_local=False, is_free=False,
                          cost_input_per_mtok=self.cost_input_per_mtok,
                          cost_output_per_mtok=self.cost_output_per_mtok)]

    async def show_model(self, name: str) -> dict[str, Any]:
        return {}

    async def chat_stream(self, model, messages, options, cancel):
        self.chat_calls += 1
        yield StreamChunk(content="you should never see this", done=True,
                          input_tokens=1, output_tokens=1)


@pytest.fixture
def fake_ollama() -> FakeOllamaProvider:
    return FakeOllamaProvider()


@pytest.fixture
async def services_env(db, test_settings, fake_ollama):
    """Service layer objects (no HTTP)."""
    settings_service = SettingsService(SettingsRepo(db), test_settings)
    await settings_service.set("default_model", test_settings.default_model)
    registry = ProviderRegistry()
    registry.register(fake_ollama)
    router = ModelRouter(registry, ModelsRepo(db), settings_service)
    guard = CostGuard(settings_service)
    return Simple(db=db, settings=test_settings, settings_service=settings_service,
                  registry=registry, router=router, guard=guard,
                  models=ModelsRepo(db), ollama=fake_ollama)


class Simple:
    def __init__(self, **kw):
        self.__dict__.update(kw)


@pytest.fixture
async def tools_env(db, tmp_path):
    """Tool registry + gateway executor + sandbox workspace (no HTTP)."""
    registry = ToolRegistry()
    register_builtin_tools(registry)
    executions = ExecutionsRepo(db)
    executor = ToolExecutor(registry, executions)
    ctx = ToolContext(workspace_root=tmp_path / "ws", run_id="run-test")
    ctx.workspace_root.mkdir(parents=True, exist_ok=True)
    approve_all = lambda spec, args, preview: asyncio.sleep(0, result=True)  # noqa: E731
    return Simple(registry=registry, executor=executor, ctx=ctx,
                  executions=executions, approve_all=approve_all)


# ── API client ───────────────────────────────────────────────────────
@pytest.fixture
async def api(tmp_path, fake_ollama):
    """Full ASGI app with fake provider, plus client + services."""
    import httpx
    settings = Settings(data_dir=tmp_path / "data",
                        default_model=DEFAULT_MODEL_NAME,
                        free_only=True, max_spend=0.0,
                        ollama_host="http://testserver")
    app = create_app(settings)
    svc = app.state.services
    svc.providers_registry.register(fake_ollama)  # replace real network provider
    # lifespan normally does this:
    await svc.db.connect()
    await migrate(svc.db)
    # base_url must be a loopback host: the Host-header guard (P0) rejects
    # anything outside the allowlist, exactly as in production.
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
        yield Simple(client=client, app=app, svc=svc, ollama=fake_ollama,
                     settings=settings)
    await svc.db.close()


def parse_sse(text: str) -> list[dict]:
    import json
    events = []
    for block in text.split("\n\n"):
        for line in block.splitlines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
    return events
