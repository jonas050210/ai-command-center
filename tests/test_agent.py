"""Agent Mode (P3) — end-to-end tool loop through the real API.

Covers: run lifecycle over SSE, human approval flow (approve + deny),
capability gating, sandbox escapes, circuit breaker, cooperative stop,
tool allowlist, executor gateway ordering, audit trail, and context
compaction. The fake provider is fully scripted — no network, no real
LLM — but every other layer (router, guard, engine, executor, sandbox,
db, API) is the production code path.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator

import pytest

from tests.conftest import parse_sse

from backend.app.db.repo import ExecutionsRepo
from backend.app.providers.base import (ChatMessage, ChatOptions, ModelInfo,
                                        Provider, ProviderStatus, StreamChunk)
from backend.app.security.permissions import Capability, PermissionPolicy
from backend.app.services.context import compact_messages
from backend.app.tools.builtin import (ALLOWED_PROGRAMS, check_command_allowed,
                                       register_builtin_tools)
from backend.app.tools.executor import ToolExecutor
from backend.app.tools.registry import ToolContext, ToolRegistry

ALL_CAPS = {Capability.FILESYSTEM_READ, Capability.FILESYSTEM_WRITE,
            Capability.COMMAND_EXECUTE, Capability.NETWORK_FETCH,
            Capability.GIT_OPERATE}


# ── scripted provider ────────────────────────────────────────────────
class ToolScriptProvider(Provider):
    """Deterministic provider: each chat call pops one scripted step.

    Step shape: {"text": str, "tool_calls": [{id, function:{name, arguments}}]}
    A step may also set "wait_for_cancel": the stream then idles until the
    engine's cancel event fires (deterministic stop-test).
    """

    name = "ollama"
    display_name = "Scripted (test)"
    is_local = True
    cost_input_per_mtok = 0.0
    cost_output_per_mtok = 0.0
    supports_pull = False
    supports_delete = False

    def __init__(self, script: list[dict[str, Any]]):
        self.script = list(script)
        self.chat_calls = 0
        self.last_options: ChatOptions | None = None
        self.requests: list[list[ChatMessage]] = []

    async def status(self) -> ProviderStatus:
        return ProviderStatus(name=self.name, status="running", version="test")

    async def list_models(self) -> list[ModelInfo]:
        return [ModelInfo(provider=self.name, name="script:1",
                          display_name="script:1", context_length=8192,
                          capabilities=["completion", "tools"])]

    async def show_model(self, name: str) -> dict[str, Any]:
        return {"capabilities": ["completion", "tools"],
                "model_info": {"context_length": 8192}}

    async def chat_stream(self, model: str, messages: list[ChatMessage],
                          options: ChatOptions,
                          cancel: asyncio.Event) -> AsyncIterator[StreamChunk]:
        self.chat_calls += 1
        self.last_options = options
        self.requests.append(list(messages))
        spec = self.script.pop(0) if self.script else {"text": "(script exhausted)"}
        if spec.get("wait_for_cancel"):
            while not cancel.is_set():
                await asyncio.sleep(0.01)
            yield StreamChunk(content="", done=True, input_tokens=1, output_tokens=0)
            return
        text = spec.get("text") or ""
        calls = spec.get("tool_calls")
        if text:
            yield StreamChunk(content=text)
        yield StreamChunk(content="", done=True, input_tokens=10,
                          output_tokens=2, tool_calls=calls)


def tool_call(name: str, args: dict[str, Any], call_id: str = "call-1") -> dict:
    return {"id": call_id, "type": "function",
            "function": {"name": name, "arguments": args}}


async def collect_run(svc, task: str, model: str = "script:1",
                      provider_name: str = "ollama", on_event=None,
                      timeout_s: float = 20.0) -> list[dict]:
    """Consume the engine's run stream, reacting to events via ``on_event``.

    (HTTP-level SSE with mid-run approvals can't run on httpx ASGITransport —
    it buffers the whole body — so the interactive flows drive the engine
    directly: identical engine code path, only the 3-line SSE framing of the
    router is replaced. Router framing itself is covered by the HTTP tests.)
    """
    events: list[dict] = []
    inner = svc.agent.stream_run(task=task, provider_name=provider_name,
                                 model_name=model)
    try:
        async with asyncio.timeout(timeout_s):
            async for ev in inner:
                events.append(ev)
                if on_event is not None:
                    out = on_event(ev)
                    if asyncio.iscoroutine(out):
                        await out
    finally:
        await inner.aclose()
    return events


async def drive_run(api, task: str, approve=True, model: str = "script:1",
                    timeout_s: float = 20.0) -> list[dict]:
    """Run to completion, answering approvals as the stream raises them.

    Also cross-checks the HTTP pending/decision endpoints (run in the same
    event loop, so no transport deadlock).
    """
    async def decide(ev):
        if ev.get("type") != "approval_required":
            return
        pend = (await api.client.get("/api/agent/approvals/pending")).json()
        assert any(a["id"] == ev["approval_id"] for a in pend["approvals"]), \
            "pending approvals endpoint out of sync with the stream"
        decision = approve(ev) if callable(approve) else approve
        rd = await api.client.post(f"/api/agent/approvals/{ev['approval_id']}",
                                   json={"approve": bool(decision)})
        assert rd.status_code == 200, rd.text

    return await collect_run(api.svc, task, model=model, on_event=decide,
                             timeout_s=timeout_s)


def by_type(events, t):
    return [e for e in events if e.get("type") == t]


# ── full API flow ────────────────────────────────────────────────────
class TestAgentRunApi:
    async def test_full_run_approval_and_audit(self, api, tmp_path):
        provider = ToolScriptProvider([
            {"tool_calls": [tool_call("fs_write",
                                      {"path": "hello.txt", "content": "hello agent\n"})]},
            {"text": "Created hello.txt in the workspace."},
        ])
        api.svc.providers_registry.register(provider)

        events = await drive_run(api, "Create hello.txt", approve=True)

        types = [e["type"] for e in events]
        assert types[0] == "meta"
        meta = events[0]
        assert meta["provider"] == "ollama" and meta["model"] == "script:1"
        assert meta["capabilities"]["filesystem:write"] is True
        assert "step" in types and "tool_call" in types

        # approval was requested with an honest diff preview
        appr = by_type(events, "approval_required")
        assert len(appr) == 1
        assert appr[0]["tool"] == "fs_write"
        assert "+hello agent" in (appr[0]["preview"] or "")

        tr = by_type(events, "tool_result")
        assert tr and tr[0]["ok"] is True and tr[0]["tool"] == "fs_write"
        assert tr[0]["diff"] and "hello agent" in tr[0]["diff"]

        done = by_type(events, "done")[0]
        assert done["status"] == "complete"
        assert "Created hello.txt" in done["result"]

        # real file in the sandboxed workspace
        written = api.settings.resolved_workspace_root / "hello.txt"
        assert written.read_text() == "hello agent\n"

        # tools were actually offered to the model
        assert provider.last_options is not None
        tool_names = {t["function"]["name"] for t in provider.last_options.tools}
        assert {"fs_list", "fs_read", "fs_write", "fs_edit", "shell_run"} <= tool_names

        # audit trail (executions) recorded the approved write
        r = await api.client.get("/api/agent/executions")
        rows = r.json()["executions"]
        write_rows = [x for x in rows if x["kind"] == "tool:fs_write"]
        assert write_rows and write_rows[0]["status"] == "ok"

        # run + steps persisted for history
        detail = await api.client.get(f"/api/agent/runs/{done['run_id']}")
        assert detail.status_code == 200
        body = detail.json()
        assert body["run"]["status"] == "complete"
        kinds = {s["kind"] for s in body["steps"]}
        assert {"model", "tool_call", "tool_result", "approval"} <= kinds
        assert body["approvals"][0]["status"] == "approved"

    async def test_denial_stops_run_and_writes_nothing(self, api):
        provider = ToolScriptProvider([
            {"tool_calls": [tool_call("fs_write",
                                      {"path": "nope.txt", "content": "should never land"})]},
            {"text": "unreachable"},
        ])
        api.svc.providers_registry.register(provider)

        events = await drive_run(api, "write nope.txt", approve=False)

        done = by_type(events, "done")[0]
        assert done["status"] == "denied"
        assert not (api.settings.resolved_workspace_root / "nope.txt").exists()
        assert provider.chat_calls == 1  # loop stopped immediately after denial

        audit = (await api.client.get("/api/agent/executions")).json()["executions"]
        denied = [x for x in audit if x["status"] == "denied_by_user"]
        assert denied, "denial must be audited"

    async def test_capability_off_fails_fast(self, api):
        r = await api.client.put("/api/settings",
                                 json={"cap_filesystem_write": False})
        assert r.status_code == 200
        provider = ToolScriptProvider([
            {"tool_calls": [tool_call("fs_write", {"path": "x.txt", "content": "x"})]},
            {"text": "unreachable"},
        ])
        api.svc.providers_registry.register(provider)

        events = await drive_run(api, "write x.txt", approve=True)
        done = by_type(events, "done")[0]
        assert done["status"] == "error"
        assert "not granted" in (done["error"] or "")
        assert not (api.settings.resolved_workspace_root / "x.txt").exists()
        # audited as a policy denial, NOT executed
        audit = (await api.client.get("/api/agent/executions")).json()["executions"]
        assert any(x["status"] == "denied" for x in audit)

    async def test_circuit_breaker_on_repeated_tool_errors(self, api):
        provider = ToolScriptProvider([
            {"tool_calls": [tool_call("fs_read", {"path": "../escape1.txt"}, "c1")]},
            {"tool_calls": [tool_call("fs_read", {"path": "../escape2.txt"}, "c2")]},
            {"tool_calls": [tool_call("fs_read", {"path": "../escape3.txt"}, "c3")]},
            {"text": "unreachable"},
        ])
        api.svc.providers_registry.register(provider)

        events = await drive_run(api, "read those files")
        done = by_type(events, "done")[0]
        assert done["status"] == "error"
        assert "circuit breaker" in (done["error"] or "")
        results = by_type(events, "tool_result")
        assert len(results) == 3 and all(not r["ok"] for r in results)

    async def test_stop_cancels_run_cooperatively(self, api):
        provider = ToolScriptProvider([
            {"tool_calls": [tool_call("fs_list", {"path": "."})]},
            {"wait_for_cancel": True},
        ])
        api.svc.providers_registry.register(provider)

        run_id_holder: dict[str, str] = {}
        collector = asyncio.create_task(collect_run(api.svc, "long task"))
        try:
            # wait for the run to become visible, then stop it over HTTP
            for _ in range(300):
                r = await api.client.get("/api/agent/runs")
                running = [x for x in r.json()["runs"] if x["status"] == "running"]
                if running:
                    run_id_holder["id"] = running[0]["id"]
                    break
                await asyncio.sleep(0.01)
            assert "id" in run_id_holder
            stop = await api.client.post(
                f"/api/agent/runs/{run_id_holder['id']}/stop")
            assert stop.status_code == 200 and stop.json()["stopped"] is True
            events = await asyncio.wait_for(collector, timeout=10)
        finally:
            if not collector.done():
                collector.cancel()

        done = by_type(events, "done")[0]
        assert done["status"] == "stopped"

        # stopping twice → honest 404
        again = await api.client.post(f"/api/agent/runs/{run_id_holder['id']}/stop")
        assert again.status_code == 404

    async def test_router_sse_framing_over_http(self, api):
        """Full HTTP POST → SSE framing (no tool calls → no interaction)."""
        provider = ToolScriptProvider([{"text": "plain answer"}])
        api.svc.providers_registry.register(provider)
        r = await api.client.post("/api/agent/runs",
                                  json={"task": "say hi", "provider": "ollama",
                                        "model": "script:1"})
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        assert f"data: " in r.text and "\n\n" in r.text
        events = parse_sse(r.text)
        types = [e["type"] for e in events]
        assert types[0] == "meta"
        assert "delta" in types and types[-2] == "usage" and types[-1] == "done"
        assert by_type(events, "done")[0]["status"] == "complete"

    async def test_approval_endpoint_validation(self, api):
        r = await api.client.post("/api/agent/approvals/no-such-id",
                                  json={"approve": True})
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "APPROVAL_NOT_FOUND"
        bad = await api.client.post("/api/agent/runs",
                                    json={"task": "go", "provider": "no-such-provider"})
        # unknown provider → honest error event inside the SSE envelope
        assert bad.status_code == 200
        errs = by_type(parse_sse(bad.text), "error")
        assert errs and errs[0]["code"] == "PROVIDER_NOT_FOUND"

    async def test_empty_task_rejected(self, api):
        r = await api.client.post("/api/agent/runs", json={"task": ""})
        assert r.status_code == 422

    async def test_capabilities_tools_and_history_endpoints(self, api):
        caps = (await api.client.get("/api/agent/capabilities")).json()["capabilities"]
        assert caps["filesystem:write"] is True      # default per settings
        assert caps["network:fetch"] is True         # live since P6 (Research Mode)
        tools = (await api.client.get("/api/agent/tools")).json()["tools"]
        names = {t["name"] for t in tools}
        assert names == {"fs_edit", "fs_list", "fs_read", "fs_write", "shell_run",
                         "web_search", "web_fetch"}
        web = {t["name"]: t for t in tools if t["name"].startswith("web_")}
        assert all(t["danger"] == "read" for t in web.values())
        assert all(t["capability"] == "network:fetch" for t in web.values())
        shell = next(t for t in tools if t["name"] == "shell_run")
        assert shell["danger"] == "exec" and shell["requires_approval"] is True

        missing = await api.client.get("/api/agent/runs/does-not-exist")
        assert missing.status_code == 404


# ── executor gateway (unit) — the tools_env fixture lives in conftest ──
class TestExecutorGateway:
    async def test_write_requires_approval_and_audits(self, tools_env):
        policy = PermissionPolicy(granted=set(ALL_CAPS))
        res = await tools_env.executor.execute(
            "fs_write", {"path": "a.txt", "content": "v1\n"},
            ctx=tools_env.ctx, policy=policy, approver=tools_env.approve_all)
        assert res.ok and res.diff and "+v1" in res.diff
        assert (tools_env.ctx.root / "a.txt").read_text() == "v1\n"
        rows = await tools_env.executions.list()
        assert rows[0]["kind"] == "tool:fs_write" and rows[0]["status"] == "ok"

    async def test_denied_approval_runs_nothing(self, tools_env):
        deny = lambda spec, args, preview: asyncio.sleep(0, result=False)  # noqa: E731
        policy = PermissionPolicy(granted=set(ALL_CAPS))
        res = await tools_env.executor.execute(
            "fs_write", {"path": "b.txt", "content": "x"},
            ctx=tools_env.ctx, policy=policy, approver=deny)
        assert not res.ok and "denied by the user" in res.error
        assert not (tools_env.ctx.root / "b.txt").exists()
        rows = await tools_env.executions.list()
        assert rows[0]["status"] == "denied_by_user"

    async def test_policy_denial_precedes_everything(self, tools_env):
        policy = PermissionPolicy(granted=set())  # nothing granted
        res = await tools_env.executor.execute(
            "fs_read", {"path": "a.txt"}, ctx=tools_env.ctx, policy=policy,
            approver=tools_env.approve_all)
        assert not res.ok and "not granted" in res.error
        rows = await tools_env.executions.list()
        assert rows[0]["status"] == "denied"

    async def test_missing_required_arg_rejected(self, tools_env):
        policy = PermissionPolicy(granted={Capability.FILESYSTEM_READ})
        res = await tools_env.executor.execute(
            "fs_read", {}, ctx=tools_env.ctx, policy=policy,
            approver=tools_env.approve_all)
        assert not res.ok and "missing required argument" in res.error

    async def test_unknown_tool_rejected(self, tools_env):
        policy = PermissionPolicy(granted=set(ALL_CAPS))
        res = await tools_env.executor.execute(
            "rm_everything", {}, ctx=tools_env.ctx, policy=policy,
            approver=tools_env.approve_all)
        assert not res.ok and "unknown tool" in res.error

    async def test_shell_static_gate_before_approval(self, tools_env):
        policy = PermissionPolicy(granted=set(ALL_CAPS))
        asked = []

        async def spy_approver(spec, args, preview):
            asked.append(args)
            return True

        res = await tools_env.executor.execute(
            "shell_run", {"command": "rm -rf /"},
            ctx=tools_env.ctx, policy=policy, approver=spy_approver)
        assert not res.ok and "hard-blocked" in res.error
        assert asked == [], "blocked command must never reach the approver"


# ── command allowlist / sandbox ──────────────────────────────────────
class TestCommandGate:
    def test_allowed_program(self):
        assert check_command_allowed("pytest tests/ -q", PermissionPolicy()) is None
        assert check_command_allowed("git status", PermissionPolicy()) is None
        assert "python" in ALLOWED_PROGRAMS

    def test_hard_blocked_program(self):
        err = check_command_allowed("rm -rf /", PermissionPolicy())
        assert err and "hard-blocked" in err

    def test_not_allowlisted_program(self):
        err = check_command_allowed("curl https://evil.example", PermissionPolicy())
        assert err and "not on the command allowlist" in err

    def test_chaining_rejected(self):
        for cmd in ("ls && rm x", "ls || echo x", "ls ; echo x", "ls | cat"):
            assert check_command_allowed(cmd, PermissionPolicy()) is not None, cmd

    def test_dangerous_arg_pattern(self):
        err = check_command_allowed("echo rm -rf /", PermissionPolicy())
        assert err and "dangerous pattern" in err


class TestSandboxAndFsTools:
    async def test_escapes_blocked(self, tools_env):
        policy = PermissionPolicy(granted=set(ALL_CAPS))
        for path in ("../escape.txt", "..\\escape.txt",
                     "C:\\Windows\\system32\\x", "\\\\server\\share\\x",
                     "/etc/passwd"):
            res = await tools_env.executor.execute(
                "fs_read", {"path": path}, ctx=tools_env.ctx, policy=policy,
                approver=tools_env.approve_all)
            assert not res.ok, path
            assert ("blocked" in (res.error or "") or "not found" in (res.error or "").lower()), (path, res.error)

    async def test_fs_edit_requires_unique_match(self, tools_env, tmp_path):
        policy = PermissionPolicy(granted=set(ALL_CAPS))
        target = tools_env.ctx.root / "dup.txt"
        target.write_text("foo bar foo\n")
        res = await tools_env.executor.execute(
            "fs_edit", {"path": "dup.txt", "old_text": "foo", "new_text": "baz"},
            ctx=tools_env.ctx, policy=policy, approver=tools_env.approve_all)
        assert not res.ok and "unique" in res.error
        res = await tools_env.executor.execute(
            "fs_edit", {"path": "dup.txt", "old_text": "bar foo", "new_text": "BAZ"},
            ctx=tools_env.ctx, policy=policy, approver=tools_env.approve_all)
        assert res.ok and res.diff and "-foo bar foo" in res.diff
        assert target.read_text() == "foo BAZ\n"

    async def test_shell_run_timeout_and_output(self, tools_env):
        policy = PermissionPolicy(granted=set(ALL_CAPS))
        res = await tools_env.executor.execute(
            "shell_run",
            {"command": 'python3 -c "import time\ntime.sleep(3)"', "timeout_s": 1},
            ctx=tools_env.ctx, policy=policy, approver=tools_env.approve_all)
        assert not res.ok and "timed out" in res.error

        ok = await tools_env.executor.execute(
            "shell_run", {"command": "echo hello-sandbox"},
            ctx=tools_env.ctx, policy=policy, approver=tools_env.approve_all)
        assert ok.ok and "hello-sandbox" in ok.output
        # cwd really is the sandbox
        pwd = await tools_env.executor.execute(
            "shell_run", {"command": "pwd"},
            ctx=tools_env.ctx, policy=policy, approver=tools_env.approve_all)
        assert str(tools_env.ctx.root) in pwd.output.replace("\\", "/")


# ── context compaction (P2) ──────────────────────────────────────────
class TestContextCompaction:
    async def test_under_budget_untouched(self):
        msgs = [ChatMessage(role="system", content="rules"),
                ChatMessage(role="user", content="hi")]
        out, compacted = await compact_messages(msgs, num_ctx=8000)
        assert compacted is False and out == msgs

    async def test_over_budget_compacts_with_summary(self):
        sys_msg = ChatMessage(role="system", content="rules")
        old = [ChatMessage(role="user", content=f"old turn {i} " + "x" * 200)
               for i in range(30)]
        tail = [ChatMessage(role="user", content="recent question")]
        msgs = [sys_msg, *old, *tail]

        async def fake_summarize(omitted):
            return f"SUMMARY of {len(omitted)} messages"

        out, compacted = await compact_messages(msgs, num_ctx=400,
                                                summarize=fake_summarize)
        assert compacted is True
        assert out[0].content == "rules"                    # head preserved
        assert out[1].role == "user" and "SUMMARY of" in out[1].content
        assert out[-1].content == "recent question"         # tail preserved
        assert len(out) < len(msgs)

    async def test_over_budget_trim_fallback_without_summarizer(self):
        msgs = [ChatMessage(role="user", content=f"turn {i} " + "y" * 300)
                for i in range(40)]
        out, compacted = await compact_messages(msgs, num_ctx=300, summarize=None)
        assert compacted is True
        assert any("omitted" in (m.content or "") for m in out)
