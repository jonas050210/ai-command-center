"""Hardening tests — CostGuard on model tests, metered auto-title, message
editing, git init, command-allowlist tightening, compare streaming."""
import pytest

from backend.app.core.errors import BadRequest
from tests.conftest import FakePaidProvider, parse_sse


# ── CostGuard: /api/models/test must never reach a paid provider ─────
async def test_model_test_paid_model_blocked_before_network(api):
    paid = FakePaidProvider()
    api.svc.providers_registry.register(paid)
    await api.client.post("/api/models/refresh")  # syncs ollama + paidtest
    r = await api.client.post("/api/models/test",
                              json={"provider": "paidtest",
                                    "name": "paid-model-1"})
    assert r.status_code == 403, r.text
    body = r.json()
    assert body["error"]["code"] == "PAID_MODEL_BLOCKED"
    assert paid.chat_calls == 0  # guard ran BEFORE any provider request


async def test_model_test_free_is_metered_and_ledgered(api):
    await api.client.post("/api/models/refresh")
    r = await api.client.post("/api/models/test",
                              json={"provider": "ollama", "name": "qwen3:0.6b"})
    assert r.status_code == 200, r.text
    result = r.json()
    assert result["token_method"] == "exact" or result["token_method"] == "estimated"
    assert result["cost_eur"] == 0.0
    # the test inference lands in the usage ledger (single metered path)
    totals = (await api.client.get("/api/usage/tokens")).json()["total"]
    assert totals["total_tokens"] > 0


# ── Auto-title: metered, guarded, settled, and applied ───────────────
async def test_auto_title_goes_through_runner_and_applies(api):
    await api.client.post("/api/models/refresh")
    r = await api.client.post("/api/chat/completions",
                              json={"content": "A question about space"})
    events = parse_sse(r.text)
    conv_id = events[0]["conversation_id"]
    # let the tracked background auto-title task complete
    await api.svc.requests.shutdown(timeout=5.0)
    conv = (await api.client.get(f"/api/conversations/{conv_id}")).json()
    # fake model says "Hello from a fake local model." → real title
    assert "fake local model" in conv["title"], conv["title"]
    # the title inference was recorded as a second usage event
    totals = (await api.client.get("/api/usage/tokens")).json()["total"]
    assert totals["total_tokens"] > 0
    # session metrics also account for it
    costs = (await api.client.get("/api/costs")).json()
    assert costs["session"] == 0.0 and costs["total"] == 0.0


# ── Message editing ──────────────────────────────────────────────────
async def make_conversation(api, content="first message"):
    await api.client.post("/api/models/refresh")
    r = await api.client.post("/api/chat/completions", json={"content": content})
    return parse_sse(r.text)[0]["conversation_id"]


async def test_edit_user_message_truncates_followups(api):
    cid = await make_conversation(api)
    conv = (await api.client.get(f"/api/conversations/{cid}")).json()
    user_msg = next(m for m in conv["messages"] if m["role"] == "user")
    assert len(conv["messages"]) == 2  # user + assistant

    r = await api.client.patch(f"/api/messages/{user_msg['id']}",
                               json={"content": "edited question"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["message"]["content"] == "edited question"
    assert body["truncated_messages"] == 1  # assistant after it was removed
    assert body["conversation"]["total_tokens"] == 0  # recounted honestly

    conv = (await api.client.get(f"/api/conversations/{cid}")).json()
    assert [m["role"] for m in conv["messages"]] == ["user"]
    assert conv["messages"][0]["content"] == "edited question"


async def test_edit_assistant_message_in_place(api):
    cid = await make_conversation(api)
    conv = (await api.client.get(f"/api/conversations/{cid}")).json()
    assistant = next(m for m in conv["messages"] if m["role"] == "assistant")
    r = await api.client.patch(f"/api/messages/{assistant['id']}",
                               json={"content": "corrected response"})
    assert r.status_code == 200
    body = r.json()
    assert body["truncated_messages"] == 0
    assert body["message"]["content"] == "corrected response"
    # tokens preserved for the corrected assistant message
    assert body["message"]["input_tokens"] is not None


async def test_edit_missing_message_404(api):
    r = await api.client.patch("/api/messages/nope", json={"content": "x"})
    assert r.status_code == 404
    r = await api.client.patch("/api/messages/nope", json={"content": ""})
    assert r.status_code == 422


# ── System prompt can be explicitly cleared ──────────────────────────
async def test_system_prompt_can_be_cleared(api):
    conv = (await api.client.post("/api/conversations",
                                  json={"title": "Sys", "system_prompt": "Be terse."})).json()
    r = await api.client.patch(f"/api/conversations/{conv['id']}",
                               json={"system_prompt": None})
    assert r.status_code == 200
    assert r.json()["system_prompt"] == ""


# ── Git init is real ─────────────────────────────────────────────────
async def test_git_init_creates_repo(api):
    project = (await api.client.post("/api/projects",
                                     json={"name": "InitRepo"})).json()
    r = await api.client.post("/api/git/init", params={"project_id": project["id"]})
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True and r.json()["already"] is False
    st = (await api.client.get("/api/git/status",
                               params={"project_id": project["id"]})).json()
    assert st["is_repo"] is True
    # idempotent second call
    r2 = await api.client.post("/api/git/init", params={"project_id": project["id"]})
    assert r2.json()["already"] is True


# ── Command allowlist tightening ─────────────────────────────────────
async def test_npx_and_npm_exec_blocked(db, tmp_path):
    from backend.app.db.repo import ExecutionsRepo
    from backend.app.tools.runner import CommandRunner
    ws = tmp_path / "ws"
    ws.mkdir(parents=True)
    runner = CommandRunner(ws, ExecutionsRepo(db))
    with pytest.raises(BadRequest) as exc:
        runner.validate("npx cowsay hi")
    assert exc.value.code == "COMMAND_NOT_ALLOWED"
    with pytest.raises(BadRequest) as exc:
        runner.validate("npm exec eslint")
    assert exc.value.code == "COMMAND_NOT_ALLOWED"
    with pytest.raises(BadRequest) as exc:
        runner.validate("npm install")
    assert exc.value.code == "COMMAND_NOT_ALLOWED"
    # still allowed
    assert runner.validate("npm run test")[1] == "run"
    assert runner.validate("npm test")[1] == "test"
    # Windows drive-absolute references are rejected even on POSIX hosts
    # (either the shell-meta or the arg-escape layer must block them)
    for evil in ("python C:\\Windows\\System32\\evil.py",
                 "pytest D:\\something", "C:\\evil.exe"):
        with pytest.raises(BadRequest):
            runner.validate(evil)
    # bare forward-slash drive syntax hits the dedicated check
    with pytest.raises(BadRequest) as exc:
        runner.validate("python C:/Windows/evil.py")
    assert exc.value.code == "ARG_ESCAPE_BLOCKED"


# ── Compare streams real deltas now ──────────────────────────────────
async def test_compare_streams_deltas(api):
    await api.client.post("/api/models/refresh")
    r = await api.client.post("/api/compare/runs", json={
        "prompt": "Say something interesting.",
        "models": ["qwen3:0.6b", "deepseek-r1:7b"],
    })
    assert r.status_code == 200, r.text
    events = parse_sse(r.text)
    deltas = [e for e in events if e["type"] == "delta"]
    assert deltas, "compare must stream generation deltas to the browser"
    assert {d["model"] for d in deltas} == {"qwen3:0.6b", "deepseek-r1:7b"}
    assert events[-1]["type"] == "done"
