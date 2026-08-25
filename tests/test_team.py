"""Team Mode (P5) — planner → executor → reviewer over the real engine."""
from __future__ import annotations

import asyncio


from tests.conftest import parse_sse
from tests.test_agent import ToolScriptProvider, tool_call


async def make_team(api, roles=("planner", "executor", "reviewer")):
    members = [{"role": r, "model": "script:1", "provider": "ollama",
                "responsibility": f"{r} job"} for r in roles]
    r = await api.client.post("/api/team", json={"name": "QA Cell", "members": members})
    assert r.status_code == 201, r.text
    return r.json()["team"]


def by_type(events, t):
    return [e for e in events if e.get("type") == t]


class TestTeamCrud:
    async def test_validation(self, api):
        bad = await api.client.post("/api/team", json={
            "name": "x", "members": [
                {"role": "planner", "model": "script:1"},
                {"role": "wizard", "model": "script:1"}]})
        assert bad.status_code == 422  # schema pattern rejects unknown roles

        no_exec = await api.client.post("/api/team", json={
            "name": "x", "members": [
                {"role": "planner", "model": "m"},
                {"role": "reviewer", "model": "m"}]})
        assert no_exec.status_code == 400
        assert no_exec.json()["error"]["code"] == "TEAM_EXECUTOR"

        two_exec = await api.client.post("/api/team", json={
            "name": "x", "members": [
                {"role": "executor", "model": "m"},
                {"role": "executor", "model": "m"}]})
        assert two_exec.status_code == 400

        ghost = await api.client.post("/api/team", json={
            "name": "x", "members": [
                {"role": "planner", "model": "m", "provider": "nope"},
                {"role": "executor", "model": "m"}]})
        assert ghost.status_code == 400
        assert ghost.json()["error"]["code"] == "PROVIDER_NOT_FOUND"

    async def test_list_get_delete(self, api):
        team = await make_team(api)
        listed = await api.client.get("/api/team")
        assert any(t["id"] == team["id"] for t in listed.json()["teams"])
        detail = await api.client.get(f"/api/team/{team['id']}")
        assert len(detail.json()["team"]["members"]) == 3
        delete = await api.client.delete(f"/api/team/{team['id']}")
        assert delete.status_code == 200
        assert (await api.client.get(f"/api/team/{team['id']}")).status_code == 404


class TestTeamRun:
    async def test_full_pipeline_accept(self, api):
        provider = ToolScriptProvider([
            {"text": "1. Write hello.py. 2. Run it."},                      # planner
            {"tool_calls": [tool_call("fs_write",
                                      {"path": "hello.py", "content": "print('hi')\n"})]},
            {"text": "Wrote hello.py."},                                    # executor step 2
            {"text": "VERDICT: ACCEPTED\nSolid work."},                     # reviewer
        ])
        api.svc.providers_registry.register(provider)
        team = await make_team(api)

        events = []

        async def on_event(ev):
            if ev["type"] == "member_event" and ev["event"].get("type") == "approval_required":
                await api.svc.agent.decide_approval(ev["event"]["approval_id"], True)

        inner = api.svc.team.stream_run(team_id=team["id"], task="make hello.py")
        async for ev in inner:
            events.append(ev)
            await on_event(ev)
        await inner.aclose()

        assert events[0]["type"] == "team_meta"
        assert len(events[0]["members"]) == 3

        # strict member ordering: planner → executor → reviewer
        starts = [e["role"] for e in events if e["type"] == "member_start"]
        assert starts == ["planner", "executor", "reviewer"], starts

        # executor's engine events were forwarded
        wrapped = [e["event"]["type"] for e in events if e["type"] == "member_event"]
        assert "tool_call" in wrapped and "tool_result" in wrapped, wrapped

        verdicts = by_type(events, "verdict")
        assert verdicts and verdicts[0]["verdict"] == "accepted"
        done = by_type(events, "team_done")[0]
        assert done["status"] == "complete"
        assert done["verdict"] == "accepted" and done["revision_used"] == 0
        assert done["executor_run_id"]

        # file actually written by the executor member's agent run
        written = api.settings.resolved_workspace_root / "hello.py"
        assert written.read_text() == "print('hi')\n"

        # persisted + per-member token attribution
        detail = await api.client.get(f"/api/team/runs/{done['run_id']}")
        run = detail.json()["run"]
        assert run["status"] == "complete" and run["input_tokens"] > 0
        team_after = await api.client.get(f"/api/team/{team['id']}")
        members = team_after.json()["team"]["members"]
        assert all(m["input_tokens"] > 0 for m in members), members

    async def test_reviewer_requests_revision_once(self, api):
        provider = ToolScriptProvider([
            {"text": "plan v1"},                                            # planner
            {"text": "Wrote v1."},                                          # executor (no tools)
            {"text": "VERDICT: CHANGES_REQUESTED\nFix the typo."},          # reviewer
            {"text": "Wrote v2, typo fixed."},                              # executor revision
            {"text": "VERDICT: ACCEPTED\nGood now."},                       # reviewer again
        ])
        api.svc.providers_registry.register(provider)
        team = await make_team(api)

        events = []
        inner = api.svc.team.stream_run(team_id=team["id"], task="write a note")
        async for ev in inner:
            events.append(ev)
        await inner.aclose()

        done = by_type(events, "team_done")[0]
        assert done["status"] == "complete"
        assert done["revision_used"] == 1
        assert provider.chat_calls == 5   # no unbounded loops

        exec_dones = [e for e in events if e["type"] == "member_done"
                      and e["role"] == "executor"]
        assert len(exec_dones) == 2

    async def test_team_stop_propagates_to_executor(self, api):
        provider = ToolScriptProvider([
            {"text": "plan"},                       # planner
            {"wait_for_cancel": True},              # executor blocks until cancelled
        ])
        api.svc.providers_registry.register(provider)
        team = await make_team(api, roles=("planner", "executor"))

        run_id_holder: dict[str, str] = {}
        done_holder: list[dict] = []

        async def drive():
            inner = api.svc.team.stream_run(team_id=team["id"], task="long thing")
            async for ev in inner:
                if ev["type"] == "team_meta":
                    run_id_holder["id"] = ev["run_id"]
                if ev["type"] == "team_done":
                    done_holder.append(ev)
            await inner.aclose()

        task = asyncio.create_task(drive())
        for _ in range(300):
            if "id" in run_id_holder:
                break
            await asyncio.sleep(0.01)
        assert "id" in run_id_holder
        # wait until the executor member actually starts
        for _ in range(300):
            r = await api.client.post(f"/api/team/runs/{run_id_holder['id']}/stop")
            if r.status_code == 200:
                break
            await asyncio.sleep(0.01)
        await asyncio.wait_for(task, timeout=10)
        done = done_holder[0]
        assert done["status"] == "stopped"

        # the executor's agent run row also ended stopped (not stuck "running")
        assert done["executor_run_id"]
        agent_row = await api.svc.agent.runs.get(done["executor_run_id"])
        assert agent_row["status"] == "stopped"

    async def test_team_run_over_http(self, api):
        provider = ToolScriptProvider([
            {"text": "plan: quick"},            # planner
            {"text": "executor says done"},     # executor (plain)
        ])
        api.svc.providers_registry.register(provider)
        team = await make_team(api, roles=("planner", "executor"))
        r = await api.client.post(f"/api/team/{team['id']}/runs",
                                  json={"task": "quick http run"})
        assert r.status_code == 200
        events = parse_sse(r.text)
        assert events[0]["type"] == "team_meta"
        assert events[-1]["type"] == "team_done"
        assert events[-1]["status"] == "complete"
        starts = [e["role"] for e in events if e["type"] == "member_start"]
        assert starts == ["planner", "executor"]
        # executor deltas arrive wrapped as member events
        wrapped_delta = [e for e in events if e["type"] == "member_event"
                         and e["event"].get("type") == "delta"]
        assert wrapped_delta

    async def test_unknown_team_404(self, api):
        r = await api.client.post("/api/team/99999/runs", json={"task": "x"})
        assert r.status_code == 200  # SSE envelope carries the error event
        events = parse_sse(r.text)
        errs = [e for e in events if e["type"] == "error"]
        assert errs and errs[0]["code"] == "TEAM_NOT_FOUND"
