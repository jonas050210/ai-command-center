"""Projects (P4) — named sandboxed workspaces + project-scoped agent runs."""
from __future__ import annotations

import pytest

from backend.app.core.errors import AppError
from tests.test_agent import ToolScriptProvider, tool_call


class TestAttachFolder:
    async def test_attach_existing_dir(self, api, tmp_path):
        real = tmp_path / "my-real-repo"
        real.mkdir()
        (real / "app.py").write_text("print(1)\n", encoding="utf-8")
        r = await api.client.post("/api/projects/attach",
                                  json={"path": str(real), "name": "Real"})
        assert r.status_code == 201, r.text
        proj = r.json()["project"]
        assert proj["linked"] is True
        assert proj["missing"] is False
        assert "app.py" in str((await api.client.get(
            f"/api/projects/{proj['id']}")).json()["listing"])
        # archive never deletes the folder
        await api.client.patch(f"/api/projects/{proj['id']}", json={"status": "archived"})
        assert (real / "app.py").is_file()

    async def test_attach_rejects_relative_and_missing(self, api, tmp_path):
        rel = await api.client.post("/api/projects/attach", json={"path": "relative/nope"})
        assert rel.status_code == 400
        assert rel.json()["error"]["code"] == "PATH_NOT_ABSOLUTE"
        missing = await api.client.post("/api/projects/attach",
                                        json={"path": str(tmp_path / "does-not-exist")})
        assert missing.status_code == 400
        assert missing.json()["error"]["code"] == "PATH_NOT_FOUND"

    async def test_attach_rejects_data_dir(self, api):
        r = await api.client.post("/api/projects/attach",
                                  json={"path": str(api.settings.data_dir)})
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "PATH_FORBIDDEN"

    async def test_attach_duplicate(self, api, tmp_path):
        real = tmp_path / "once"
        real.mkdir()
        a = await api.client.post("/api/projects/attach", json={"path": str(real)})
        b = await api.client.post("/api/projects/attach", json={"path": str(real)})
        assert a.status_code == 201
        assert b.status_code == 400
        assert b.json()["error"]["code"] == "PROJECT_ALREADY_LINKED"


class TestProjectsApi:
    async def test_crud_lifecycle(self, api):
        create = await api.client.post("/api/projects",
                                       json={"name": "My Tool", "description": "d"})
        assert create.status_code == 201
        proj = create.json()["project"]
        assert proj["root_path"].startswith("projects/")
        assert proj["file_count"] == 0 and proj["missing"] is False

        # directory really exists inside the workspace
        root = api.settings.resolved_workspace_root / proj["root_path"]
        assert root.is_dir()
        assert root.resolve().is_relative_to(api.settings.resolved_workspace_root)

        listing = await api.client.get("/api/projects")
        assert listing.status_code == 200
        assert any(p["id"] == proj["id"] for p in listing.json()["projects"])

        patch = await api.client.patch(f"/api/projects/{proj['id']}",
                                       json={"name": "Renamed"})
        assert patch.json()["project"]["name"] == "Renamed"

        archive = await api.client.patch(f"/api/projects/{proj['id']}",
                                         json={"status": "archived"})
        assert archive.json()["project"]["status"] == "archived"
        # archived projects disappear from the default list but files stay
        listing2 = await api.client.get("/api/projects")
        assert not any(p["id"] == proj["id"] for p in listing2.json()["projects"])
        assert root.is_dir(), "archiving must never delete user files"

    async def test_slug_dedupe_and_validation(self, api):
        a = await api.client.post("/api/projects", json={"name": "Same"})
        b = await api.client.post("/api/projects", json={"name": "Same"})
        assert a.status_code == b.status_code == 201
        assert a.json()["project"]["root_path"] != b.json()["project"]["root_path"]

        assert (await api.client.post(
            "/api/projects", json={"name": "   "})).status_code in (400, 422)
        assert (await api.client.get("/api/projects/999999")).status_code == 404
        assert (await api.client.patch("/api/projects/999999",
                                       json={"name": "x"})).status_code == 404


async def run_project_run(api, project_id, script=None):
    provider = ToolScriptProvider(script or [
        {"tool_calls": [tool_call("fs_write",
                                  {"path": "code/main.py", "content": "print(1)\n"})]},
        {"text": "created"},
    ])
    api.svc.providers_registry.register(provider)
    events = []
    inner = api.svc.agent.stream_run(task="make code/main.py",
                                     provider_name="ollama", model_name="script:1",
                                     project_id=project_id)
    try:
        async for ev in inner:
            events.append(ev)
            if ev.get("type") == "approval_required":
                await api.svc.agent.decide_approval(ev["approval_id"], True)
    finally:
        await inner.aclose()
    return events


class TestProjectAgentRuns:
    async def test_run_scoped_to_project_root(self, api):
        proj = (await api.client.post("/api/projects", json={"name": "Scoped"})
                ).json()["project"]
        events = await run_project_run(api, proj["id"])

        meta = next(e for e in events if e["type"] == "meta")
        assert meta["project"] == {"id": proj["id"], "name": "Scoped"}
        done = next(e for e in events if e["type"] == "done")
        assert done["status"] == "complete"

        proj_root = api.settings.resolved_workspace_root / proj["root_path"]
        assert (proj_root / "code" / "main.py").read_text() == "print(1)\n"
        # global workspace must NOT have the file — the run was confined
        assert not (api.settings.resolved_workspace_root / "code").exists()

        # escape attempts resolve against the PROJECT root, not the workspace
        esc = ToolScriptProvider([
            {"tool_calls": [tool_call("fs_read", {"path": "../outside.txt"})]},
            {"text": "gave up"},
        ])
        api.svc.providers_registry.register(esc)
        events2 = []
        inner = api.svc.agent.stream_run(task="peek outside", provider_name="ollama",
                                         model_name="script:1", project_id=proj["id"])
        async for ev in inner:
            events2.append(ev)
        await inner.aclose()
        tr = next(e for e in events2 if e["type"] == "tool_result")
        assert tr["ok"] is False and "blocked" in (tr["output"] or "")

        # runs are linked to the project
        runs = await api.client.get(f"/api/projects/{proj['id']}/runs")
        assert runs.status_code == 200
        assert all(r["project_id"] == proj["id"] for r in runs.json()["runs"])
        assert len(runs.json()["runs"]) == 2

    async def test_unknown_project_rejected(self, api):
        api.svc.providers_registry.register(ToolScriptProvider([{"text": "x"}]))
        with pytest.raises(AppError) as ei:
            async for _ in api.svc.agent.stream_run(task="x", provider_name="ollama",
                                                    model_name="script:1",
                                                    project_id=424242):
                pass
        assert ei.value.code == "PROJECT_NOT_FOUND"

    async def test_archived_project_rejected(self, api):
        api.svc.providers_registry.register(ToolScriptProvider([{"text": "x"}]))
        proj = (await api.client.post("/api/projects", json={"name": "Arch"})
                ).json()["project"]
        await api.client.patch(f"/api/projects/{proj['id']}", json={"status": "archived"})
        with pytest.raises(AppError) as ei:
            async for _ in api.svc.agent.stream_run(task="x", provider_name="ollama",
                                                    model_name="script:1",
                                                    project_id=proj["id"]):
                pass
        assert ei.value.code == "PROJECT_ARCHIVED"
