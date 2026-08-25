"""P12 run snapshots — undo restores only what the run actually changed."""
from __future__ import annotations

from tests.test_agent import ToolScriptProvider, drive_run, by_type, tool_call


class TestRunUndo:
    async def test_undo_restores_edit_and_deletes_created(self, api):
        root = api.settings.resolved_workspace_root
        (root / "keep.py").write_text("old\n", encoding="utf-8")
        provider = ToolScriptProvider([
            {"tool_calls": [
                tool_call("fs_write", {"path": "keep.py", "content": "new\n"}, "c1"),
                tool_call("fs_write", {"path": "fresh.py", "content": "created\n"}, "c2"),
            ]},
            {"text": "done"},
        ])
        api.svc.providers_registry.register(provider)
        events = await drive_run(api, "edit keep and add fresh")
        done = by_type(events, "done")[0]
        assert done["status"] == "complete"
        assert (root / "keep.py").read_text() == "new\n"
        assert (root / "fresh.py").read_text() == "created\n"

        snap = await api.client.get(f"/api/agent/runs/{done['run_id']}/snapshot")
        assert snap.status_code == 200
        assert snap.json()["exists"] is True
        paths = {f["path"] for f in snap.json()["files"]}
        assert paths == {"keep.py", "fresh.py"}

        undo = await api.client.post(f"/api/agent/runs/{done['run_id']}/undo")
        assert undo.status_code == 200, undo.text
        assert undo.json()["restored"] == 1
        assert undo.json()["deleted"] == 1
        assert (root / "keep.py").read_text() == "old\n"
        assert not (root / "fresh.py").exists()

    async def test_undo_empty_run(self, api):
        provider = ToolScriptProvider([{"text": "nothing to change"}])
        api.svc.providers_registry.register(provider)
        events = await drive_run(api, "just talk")
        rid = by_type(events, "done")[0]["run_id"]
        r = await api.client.post(f"/api/agent/runs/{rid}/undo")
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "SNAPSHOT_EMPTY"

    async def test_undo_unknown_run(self, api):
        r = await api.client.post("/api/agent/runs/nope/undo")
        assert r.status_code == 400
