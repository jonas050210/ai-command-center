"""Memory + skills (P8) — persistent memory, AGENT.md, prompt injection."""
from __future__ import annotations

import asyncio

import pytest

from backend.app.db.repo import MemoriesRepo
from backend.app.memory.service import (AGENT_MD_NAME, MemoryService)
from backend.app.security.permissions import Capability, PermissionPolicy
from tests.conftest import parse_sse
from tests.test_agent import ALL_CAPS, ToolScriptProvider, tool_call


@pytest.fixture
async def mem(db):
    return MemoryService(MemoriesRepo(db))


class TestMemoryService:
    async def test_save_list_forget(self, mem):
        await mem.save("project stack", "Backend FastAPI, frontend React")
        await mem.save("style", "direct, technical")
        rows = await mem.list()
        keys = {r["key"] for r in rows}
        assert keys == {"project stack", "style"}
        found = await mem.search("fastapi")
        assert found and found[0]["key"] == "project stack"
        assert await mem.forget("style") is True
        assert await mem.forget("style") is False
        assert {r["key"] for r in await mem.list()} == {"project stack"}

    async def test_upsert_and_validation(self, mem):
        await mem.save("k", "v1")
        await mem.save("k", "v2")
        rows = await mem.list()
        assert len(rows) == 1 and rows[0]["content"] == "v2"
        with pytest.raises(ValueError):
            await mem.save("bad;key!", "x")
        with pytest.raises(ValueError):
            await mem.save("ok", "   ")

    async def test_agent_md_and_skills(self, mem, tmp_path):
        assert mem.read_agent_md(tmp_path) is None
        (tmp_path / AGENT_MD_NAME).write_text("Always run pytest first.",
                                             encoding="utf-8")
        assert "pytest" in (mem.read_agent_md(tmp_path) or "")
        skills = await mem.build_skills_text(tmp_path)
        assert skills and "Always run pytest first." in skills
        proj = tmp_path / "projects" / "p1"
        proj.mkdir(parents=True)
        (proj / AGENT_MD_NAME).write_text("Project: use ruff.",
                                          encoding="utf-8")
        both = await mem.build_skills_text(tmp_path, proj)
        assert "workspace" in both and "project" in both and "ruff" in both

    async def test_memory_text_capped_honestly(self, mem):
        for i in range(30):
            await mem.save(f"fact {i}", "x" * 400)
        text = await mem.memory_text()
        assert text is not None and "truncated" in text


class TestMemoryTools:
    async def test_save_requires_capability_and_approval(self, tools_env, mem):
        tools_env.ctx.memory = mem
        # capability off → denied + audited
        policy = PermissionPolicy(granted={Capability.FILESYSTEM_READ})
        res = await tools_env.executor.execute(
            "memory_save", {"key": "k", "content": "v"}, ctx=tools_env.ctx,
            policy=policy, approver=tools_env.approve_all)
        assert not res.ok and "not granted" in (res.error or "")
        # capability on but user denies → nothing lands
        deny = lambda spec, args, preview: asyncio.sleep(0, result=False)  # noqa: E731
        policy = PermissionPolicy(granted=set(ALL_CAPS))
        res = await tools_env.executor.execute(
            "memory_save", {"key": "k", "content": "v"}, ctx=tools_env.ctx,
            policy=policy, approver=deny)
        assert not res.ok and await mem.list() == []
        # approved → stored with agent provenance
        res = await tools_env.executor.execute(
            "memory_save", {"key": "k", "content": "v"}, ctx=tools_env.ctx,
            policy=policy, approver=tools_env.approve_all)
        assert res.ok
        rows = await mem.list()
        assert rows[0]["source"].startswith("agent:")

    async def test_search_and_forget(self, tools_env, mem):
        tools_env.ctx.memory = mem
        await mem.save("owner", "Jonas")
        policy = PermissionPolicy(granted=set(ALL_CAPS))
        res = await tools_env.executor.execute(
            "memory_search", {"query": "owner"}, ctx=tools_env.ctx,
            policy=policy, approver=tools_env.approve_all)
        assert res.ok and "Jonas" in res.output
        res = await tools_env.executor.execute(
            "memory_forget", {"key": "owner"}, ctx=tools_env.ctx,
            policy=policy, approver=tools_env.approve_all)
        assert res.ok and await mem.list() == []


class TestMemoryApi:
    async def test_crud_over_http(self, api):
        r = await api.client.post("/api/memory",
                                  json={"key": "stack", "content": "FastAPI"})
        assert r.status_code == 201
        listed = (await api.client.get("/api/memory")).json()
        assert listed["count"] == 1
        mid = listed["memories"][0]["id"]
        r = await api.client.delete(f"/api/memory/{mid}")
        assert r.status_code == 200
        assert (await api.client.get("/api/memory")).json()["count"] == 0

    async def test_agent_md_roundtrip_and_context(self, api):
        r = (await api.client.get("/api/memory/file")).json()
        assert r["present"] is False
        r = await api.client.put("/api/memory/file",
                                 json={"content": "Verify with pytest."})
        assert r.status_code == 200 and r.json()["present"] is True
        got = (await api.client.get("/api/memory/file")).json()
        assert got["content"] == "Verify with pytest."
        ctx = (await api.client.get("/api/memory/context")).json()
        assert ctx["agent_md"] is True
        # empty content deletes the file honestly
        await api.client.put("/api/memory/file", json={"content": ""})
        assert (await api.client.get("/api/memory/file")).json()[
            "present"] is False

    async def test_capability_visible(self, api):
        caps = (await api.client.get("/api/agent/capabilities")).json()[
            "capabilities"]
        assert caps["memory"] is True       # default ON


class TestPromptInjection:
    async def test_run_inherits_memory_and_agent_md(self, api):
        # user-authored context exists before the run
        await api.client.put("/api/memory/file",
                             json={"content": "Always say AGENT_MD_MARK."})
        await api.client.post("/api/memory",
                              json={"key": "mark", "content": "MEMORY_MARK"})

        provider = ToolScriptProvider([{"text": "done."}])
        api.svc.providers_registry.register(provider)
        r = await api.client.post("/api/agent/runs", json={
            "task": "say hi", "model": "script:1"})
        assert r.status_code == 200
        system = provider.requests[0][0].content       # system prompt, call 1
        assert "Always say AGENT_MD_MARK." in system   # AGENT.md via skills slot
        assert "MEMORY_MARK" in system                 # persistent memory block
        assert "PERSISTENT MEMORY" in system

    async def test_model_can_save_memory_inside_run(self, api):
        provider = ToolScriptProvider([
            {"tool_calls": [tool_call("memory_save", {
                "key": "fact one", "content": "learned in run"})]},
            {"text": "saved."},
        ])
        api.svc.providers_registry.register(provider)

        events = []

        async def on_event(ev):
            if ev.get("type") == "approval_required":
                await api.svc.agent.decide_approval(ev["approval_id"], True)

        async def drain():
            async for ev in api.svc.agent.stream_run(
                    task="remember this", provider_name="ollama",
                    model_name="script:1"):
                events.append(ev)
                await on_event(ev)

        await drain()
        saved = [
            e for e in events
            if e["type"] == "tool_result" and e.get("tool") == "memory_save"]
        assert saved and saved[0]["ok"] is True
        rows = (await api.client.get("/api/memory")).json()["memories"]
        assert rows and rows[0]["key"] == "fact one"
        assert rows[0]["source"].startswith("agent:")
