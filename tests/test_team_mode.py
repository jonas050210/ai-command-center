"""Team Mode (flagship) tests — planning, roles, board, tokens, delivery."""
from tests.conftest import parse_sse


async def seed_and_team(api, models=("qwen3:0.6b", "deepseek-r1:7b", "qwen3:0.6b")):
    await api.client.post("/api/models/refresh")
    body = {
        "task": "Build a simple Python greeting module with a test.",
        "models": list(models),
    }
    r = await api.client.post("/api/team/runs", json=body)
    assert r.status_code == 200, r.text
    return parse_sse(r.text)


async def test_team_run_full_flow_with_fake_models(api):
    await api.client.post("/api/models/refresh")
    events = await seed_and_team(api, models=("qwen3:0.6b", "deepseek-r1:7b"))
    types = [e["type"] for e in events]
    assert types[0] == "team"
    assert "phase" in types
    assert "activity" in types
    assert "tokens" in types
    assert "done" in types
    final = events[-1]
    assert final["status"] == "delivered"
    assert final["deliverable"]

    team_id = events[0]["team_id"]
    state = (await api.client.get(f"/api/team/runs/{team_id}")).json()
    assert state["master_plan"]
    assert len(state["members"]) == 2
    roles = {m["model"]: m["role"] for m in state["members"]}
    assert all(v for v in roles.values())
    assert state["tasks"], "task board must be populated"
    statuses = {t["status"] for t in state["tasks"]}
    assert statuses <= {"todo", "in_progress", "review", "done"}
    totals = state["tokens"]
    assert totals["total_tokens"] > 0
    assert totals["cost_eur"] == 0.0
    # per-model accounting
    assert all(m["input_tokens"] > 0 or m["output_tokens"] > 0
               for m in state["members"])


async def test_team_rejects_wrong_size(api):
    r = await api.client.post("/api/team/runs",
                              json={"task": "x", "models": ["qwen3:0.6b"]})
    assert r.status_code == 422  # pydantic min_length=2

    body = {"task": "x", "models": []}
    r = await api.client.post("/api/team/runs", json=body)
    assert r.status_code == 422


async def test_team_state_endpoints(api):
    await api.client.post("/api/models/refresh")
    events = await seed_and_team(api, models=("qwen3:0.6b", "deepseek-r1:7b"))
    team_id = events[0]["team_id"]
    board = (await api.client.get(f"/api/team/runs/{team_id}/board")).json()
    assert set(board["board"]) == {"todo", "in_progress", "review", "done"}
    export = (await api.client.get(f"/api/team/runs/{team_id}/export")).json()
    assert "TEAM TOTAL" in export["export"]["content"]


async def test_team_manual_board_override(api):
    await api.client.post("/api/models/refresh")
    events = await seed_and_team(api, models=("qwen3:0.6b", "deepseek-r1:7b"))
    team_id = events[0]["team_id"]
    tasks = (await api.client.get(f"/api/team/runs/{team_id}/board")).json()["tasks"]
    tid = tasks[0]["id"]
    r = await api.client.patch(f"/api/team/runs/{team_id}/tasks/{tid}",
                               json={"status": "done", "progress": 100})
    assert r.status_code == 200
    after = (await api.client.get(f"/api/team/runs/{team_id}/board")).json()
    assert any(t["id"] == tid and t["status"] == "done"
               for t in after["tasks"])


async def test_team_roles_override(api):
    await api.client.post("/api/models/refresh")
    events = await seed_and_team(
        api, models=("qwen3:0.6b", "deepseek-r1:7b"))
    team_id = events[0]["team_id"]
    state = (await api.client.get(f"/api/team/runs/{team_id}")).json()
    # every member has at least one recognized role
    for m in state["members"]:
        assert m["role"].strip()
