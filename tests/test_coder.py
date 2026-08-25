"""Coder Mode (P11) — profile, sandboxed tree/file, no mutation surface."""
from __future__ import annotations

from pathlib import Path


class TestCoderContext:
    async def test_context_pack_lists_tree(self, api):
        root = api.settings.resolved_workspace_root
        (root / "main.py").write_text("x\n", encoding="utf-8")
        r = await api.client.get("/api/coder/context")
        assert r.status_code == 200
        ctx = r.json()["context"]
        assert "CODER CONTEXT" in ctx
        assert "main.py" in ctx

    async def test_coder_mode_injects_context(self, api):
        from tests.test_agent import ToolScriptProvider
        (api.settings.resolved_workspace_root / "seen.py").write_text("1\n")
        provider = ToolScriptProvider([{"text": "ok"}])
        api.svc.providers_registry.register(provider)
        events = []
        inner = api.svc.agent.stream_run(
            task="look around", provider_name="ollama", model_name="script:1",
            mode="coder")
        async for ev in inner:
            events.append(ev)
        await inner.aclose()
        notes = [e for e in events if e.get("type") == "note"]
        assert any("Coder context" in (n.get("message") or "") for n in notes)
        # the scripted provider saw the injected pack in the system prompt
        sys_content = provider.requests[0][0].content
        assert "CODER CONTEXT" in sys_content
        assert "seen.py" in sys_content


class TestCoderProfile:
    async def test_profile_honest_without_catalog(self, api):
        r = await api.client.get("/api/coder/profile")
        assert r.status_code == 200
        data = r.json()
        assert data["pull"] == "qwen2.5-coder:7b"
        assert data["hardware"]["num_ctx_default"] == 8192
        assert "Ollama is the runtime" in data["note"]
        assert "fs_edit" in data["skills"]
        assert data["selected"] is None          # nothing synced yet
        assert "qwen3-coder" not in {x.lower() for x in
                                     ([data["pull"]] + data["missing_preferred"])}

    async def test_profile_picks_installed_coder_over_smoke(self, api):
        await api.svc.models_repo.upsert_from_provider({
            "provider": "ollama", "name": "qwen3:0.6b", "display_name": "qwen3:0.6b",
            "is_local": True, "is_free": True, "capabilities": ["completion", "tools"],
            "categories": [], "available": True, "status": "available",
        })
        await api.svc.models_repo.upsert_from_provider({
            "provider": "ollama", "name": "qwen2.5-coder:7b",
            "display_name": "qwen2.5-coder:7b",
            "is_local": True, "is_free": True, "capabilities": ["completion", "tools"],
            "categories": ["coding"], "available": True, "status": "available",
        })
        await api.svc.models_repo.upsert_from_provider({
            "provider": "ollama", "name": "qwen3-coder:30b",
            "display_name": "qwen3-coder:30b",
            "is_local": True, "is_free": True, "capabilities": ["completion", "tools"],
            "categories": ["coding"], "available": True, "status": "available",
        })
        data = (await api.client.get("/api/coder/profile")).json()
        assert data["selected"]["name"] == "qwen2.5-coder:7b"
        assert "qwen3-coder:30b" in data["too_big_installed"]
        assert all(r["name"] != "qwen3-coder:30b" for r in data["recommended"])


class TestCoderTreeAndFile:
    async def test_workspace_tree_and_read(self, api):
        root: Path = api.settings.resolved_workspace_root
        (root / "hello.py").write_text("print('hi')\n", encoding="utf-8")
        (root / "node_modules").mkdir()
        (root / "node_modules" / "pkg.js").write_text("nope", encoding="utf-8")
        (root / "src").mkdir()
        (root / "src" / "app.py").write_text("x = 1\n", encoding="utf-8")

        tree = (await api.client.get("/api/coder/tree")).json()
        names = {e["name"] for e in tree["entries"]}
        assert "hello.py" in names
        assert "src" in names
        assert "node_modules" not in names
        assert tree["project"] is None

        src = next(e for e in tree["entries"] if e["name"] == "src")
        assert any(c["name"] == "app.py" for c in src["children"])

        body = (await api.client.get("/api/coder/file", params={"path": "hello.py"})).json()
        assert body["binary"] is False
        assert "print('hi')" in body["content"]
        assert body["truncated"] is False

    async def test_project_scope_and_escape(self, api):
        proj = (await api.client.post("/api/projects", json={"name": "CoderBox"})
                ).json()["project"]
        proj_root = api.settings.resolved_workspace_root / proj["root_path"]
        (proj_root / "main.py").write_text("ok\n", encoding="utf-8")
        # a sibling outside the project
        (api.settings.resolved_workspace_root / "secret.txt").write_text(
            "nope", encoding="utf-8")

        tree = (await api.client.get(
            "/api/coder/tree", params={"project_id": proj["id"]})).json()
        assert tree["project"]["id"] == proj["id"]
        assert any(e["name"] == "main.py" for e in tree["entries"])
        assert not any(e["name"] == "secret.txt" for e in tree["entries"])

        ok = await api.client.get("/api/coder/file",
                                  params={"project_id": proj["id"], "path": "main.py"})
        assert ok.status_code == 200 and ok.json()["content"] == "ok\n"

        esc = await api.client.get(
            "/api/coder/file",
            params={"project_id": proj["id"], "path": "../secret.txt"})
        assert esc.status_code == 403
        assert esc.json()["error"]["code"] == "PATH_ESCAPE_BLOCKED"

        missing = await api.client.get(
            "/api/coder/file",
            params={"project_id": proj["id"], "path": "nope.py"})
        assert missing.status_code == 400
        assert missing.json()["error"]["code"] == "FILE_NOT_FOUND"

    async def test_binary_file_not_inlined(self, api):
        root: Path = api.settings.resolved_workspace_root
        (root / "blob.bin").write_bytes(b"\x00\x01\x02\xff" * 20)
        r = await api.client.get("/api/coder/file", params={"path": "blob.bin"})
        assert r.status_code == 200
        data = r.json()
        assert data["binary"] is True
        assert data["content"] is None
        assert "Binary" in data["note"]

    async def test_archived_project_rejected(self, api):
        proj = (await api.client.post("/api/projects", json={"name": "Old"})
                ).json()["project"]
        await api.client.patch(f"/api/projects/{proj['id']}", json={"status": "archived"})
        r = await api.client.get("/api/coder/tree", params={"project_id": proj["id"]})
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "PROJECT_ARCHIVED"

    async def test_unknown_project(self, api):
        r = await api.client.get("/api/coder/tree", params={"project_id": 999999})
        assert r.status_code == 404

    async def test_no_write_surface(self, api):
        assert (await api.client.post("/api/coder/tree")).status_code == 405
        assert (await api.client.put("/api/coder/file", json={"path": "x"})).status_code == 405
        assert (await api.client.delete("/api/coder/file")).status_code == 405
