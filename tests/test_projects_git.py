"""Projects + Git/GitHub tests — first-class projects, sandboxed git, honest GitHub."""
import subprocess

import pytest


async def test_project_crud_and_workspace(api):
    r = await api.client.post("/api/projects",
                              json={"name": "Demo", "description": "d"})
    assert r.status_code == 200, r.text
    project = r.json()
    pid = project["id"]
    assert project["root_path"] == f"projects/p{pid}"

    listed = (await api.client.get("/api/projects")).json()
    assert any(p["id"] == pid for p in listed["projects"])

    files = (await api.client.get(f"/api/projects/{pid}/files")).json()
    assert files["workspace"].endswith(f"p{pid}")

    task = (await api.client.post(f"/api/projects/{pid}/tasks",
                                  json={"title": "Task one"})).json()
    assert task["title"] == "Task one"
    detail = (await api.client.get(f"/api/projects/{pid}")).json()
    assert len(detail["tasks"]) == 1
    assert detail["task_count"] == 1

    r = await api.client.patch(f"/api/projects/{pid}",
                               json={"name": "Renamed"})
    assert r.json()["name"] == "Renamed"

    r = await api.client.delete(f"/api/projects/{pid}")
    assert r.status_code == 200
    assert (await api.client.get(f"/api/projects/{pid}")).status_code == 404


async def test_project_links_chat(api):
    await api.client.post("/api/models/refresh")
    project = (await api.client.post("/api/projects",
                                     json={"name": "With chat"})).json()
    from tests.conftest import parse_sse
    events = parse_sse((await api.client.post("/api/chat/completions", json={
        "content": "hello project", "project_id": project["id"]})).text)
    conv_id = events[0]["conversation_id"]
    conv = (await api.client.get(f"/api/conversations/{conv_id}")).json()
    assert conv["project_id"] == project["id"]
    detail = (await api.client.get(f"/api/projects/{project['id']}")).json()
    assert any(c["id"] == conv_id for c in detail["conversations"])


@pytest.fixture
async def git_project(api, tmp_path):
    """Create a real git repository inside the sandbox workspace."""
    project = (await api.client.post("/api/projects",
                                     json={"name": "Repo"})).json()
    ws = api.svc.settings.resolved_workspace_root / "projects" / f"p{project['id']}"
    ws.mkdir(parents=True, exist_ok=True)
    (ws / ".gitignore").write_text("*.pyc\n")
    subprocess.run(["git", "init", "-q"], cwd=ws, check=True)
    (ws / "README.md").write_text("# repo\n")
    subprocess.run(["git", "add", "-A"], cwd=ws, check=True)
    subprocess.run(["git", "-c", "user.name=Test",
                    "-c", "user.email=t@t.local", "commit", "-qm", "initial"],
                   cwd=ws, check=True)
    (ws / "README.md").write_text("# repo changed\n")
    return project


async def test_git_status_log_diff_commit(api, git_project):
    pid = git_project["id"]
    st = (await api.client.get("/api/git/status",
                               params={"project_id": pid})).json()
    assert st["ok"] is True and st["is_repo"] is True
    assert st["branch"] == "master" or st["branch"] == "main"
    assert st["changes"] == 1

    lg = (await api.client.get("/api/git/log",
                               params={"project_id": pid})).json()
    assert any("initial" in e for e in lg["entries"])

    br = (await api.client.get("/api/git/branches",
                               params={"project_id": pid})).json()
    assert br["ok"] is True and any("master" in b or "main" in b
                                    for b in br["branches"])

    d = (await api.client.get("/api/git/diff",
                              params={"project_id": pid})).json()
    assert d["ok"] is True and "repo changed" in d["diff"]

    c = (await api.client.post("/api/git/commit", params={"project_id": pid},
                               json={"message": "update readme"})).json()
    assert c["ok"] is True
    st2 = (await api.client.get("/api/git/status",
                                params={"project_id": pid})).json()
    assert st2["clean"] is True


async def test_git_safety(api, git_project):
    pid = git_project["id"]
    # dangerous subcommands blocked server-side
    for cmd in ("push", "reset", "checkout -- .", "clean -fdx"):
        r = await api.client.post("/api/git/commit",
                                  params={"project_id": pid}, json={
                                      "message": cmd, "paths": []})
    r = await api.client.get("/api/git/branches", params={"project_id": pid})
    assert r.status_code == 200
    # bad commit message rejected
    r = await api.client.post("/api/git/commit",
                              params={"project_id": pid}, json={"message": "x"})
    assert r.status_code == 422


async def test_github_state_honest_when_no_token(api, monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    st = (await api.client.get("/api/github/state")).json()
    assert st["authenticated"] is False
    assert "token" in st["message"].lower()
    # repositories endpoint also honest
    r = (await api.client.get("/api/github/repositories")).json()
    assert r["authenticated"] is False


async def test_github_token_stored_encrypted(api, monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    r = await api.client.put("/api/github/credentials",
                             json={"token": "ghp_secret_token_123"})
    assert r.status_code == 200
    row = await api.svc.credentials_repo.get("github")
    assert row is not None
    assert "ghp_secret_token_123" not in row["ciphertext"]  # encrypted at rest
    # clearing works
    await api.client.delete("/api/github/credentials")
    assert await api.svc.credentials_repo.get("github") is None
