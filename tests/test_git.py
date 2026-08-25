"""Git/GitHub integration (P7) — real git against temp repos inside a
sandbox workspace; GitHub REST against a fake transport. No network."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from backend.app.db.repo import ExecutionsRepo
from backend.app.gitops.github import GitHubClient
from backend.app.gitops.service import (GitService, redact_url,
                                        validate_branch_name)
from backend.app.core.errors import AppError


# ── helpers ──────────────────────────────────────────────────────────
@pytest.fixture
async def git_env(db, tmp_path):
    """GitService over a fresh sandbox workspace + audit repo."""
    ws = tmp_path / "ws"
    ws.mkdir()
    from backend.app.services.settings_service import SettingsService
    from backend.app.db.repo import SettingsRepo
    from backend.app.config import Settings
    settings_service = SettingsService(SettingsRepo(db), Settings(
        data_dir=tmp_path / "data"))
    # git:operate ON by default in this fixture; tests toggle explicitly
    await settings_service.set("cap_git_operate", "true")
    svc = GitService(executions=ExecutionsRepo(db), workspace_root=ws,
                     data_dir=tmp_path / "data", settings=settings_service)
    return SimpleNS(svc=svc, ws=ws, settings=settings_service,
                    executions=ExecutionsRepo(db))


class SimpleNS:
    def __init__(self, **kw):
        self.__dict__.update(kw)


async def make_repo(svc: GitService, path: str = ".") -> Path:
    """Real initialized repo with one commit, inside the workspace."""
    root = svc.resolve_repo(path)
    root.mkdir(parents=True, exist_ok=True)
    await svc.init(path)
    (root / "hello.txt").write_text("v1\n", encoding="utf-8")
    await svc.commit(path, "initial commit")
    return root


# ── pure validation helpers ──────────────────────────────────────────
class TestValidators:
    def test_redact_url(self):
        assert redact_url("https://x-access-token:ghp_secret@github.com/u/r.git"
                          ) == "https://***@github.com/u/r.git"
        assert redact_url("git@github.com:u/r.git") == "git@github.com:u/r.git"
        assert redact_url("https://github.com/u/r.git") == (
            "https://github.com/u/r.git")

    def test_branch_names(self):
        assert validate_branch_name("feature/x-1") == "feature/x-1"
        for bad in ("-d", "--help", "a..b", "with space", "ends.lock",
                    "trail/", "-", "", " semi;colon"):
            with pytest.raises(AppError):
                validate_branch_name(bad)


# ── local git operations ─────────────────────────────────────────────
class TestGitLocal:
    async def test_not_a_repo_is_honest(self, git_env):
        with pytest.raises(AppError) as ei:
            await git_env.svc.status(".")
        assert ei.value.code == "GIT_NOT_A_REPO"

    async def test_path_escape_blocked_and_audited(self, git_env):
        for bad in ("../outside", "..", "sub/../../.."):
            with pytest.raises(AppError) as ei:
                await git_env.svc.status(bad)
            assert ei.value.code == "PATH_ESCAPE_BLOCKED"

    async def test_init_commit_status_log_flow(self, git_env):
        root = await make_repo(git_env.svc)
        assert (root / ".git").is_dir()

        st = await git_env.svc.status(".")
        assert st["clean"] is True
        assert st["files"] == []
        assert st["path"] == "."

        log = await git_env.svc.log(".")
        assert log["count"] == 1
        assert log["commits"][0]["message"] == "initial commit"
        assert log["commits"][0]["author"] == "AI Command Center"

        # change → status shows it, diff shows it, commit clears it
        (root / "hello.txt").write_text("v2\n", encoding="utf-8")
        (root / "new.py").write_text("print('x')\n", encoding="utf-8")
        st = await git_env.svc.status(".")
        paths = {f["path"] for f in st["files"]}
        assert paths == {"hello.txt", "new.py"}
        untracked = [f for f in st["files"] if f["untracked"]]
        assert [f["path"] for f in untracked] == ["new.py"]

        d = await git_env.svc.diff(".", file="hello.txt")
        assert "+v2" in d["diff"] and d["truncated"] is False

        res = await git_env.svc.commit(".", "second commit",
                                       files=["hello.txt"])
        assert res["committed"] and res["sha"]
        st = await git_env.svc.status(".")
        assert {f["path"] for f in st["files"]} == {"new.py"}  # only leftover

    async def test_branch_lifecycle(self, git_env):
        await make_repo(git_env.svc)
        out = await git_env.svc.create_branch(".", "feature/demo")
        assert out["created"] and out["switched"]
        branches = await git_env.svc.branches(".")
        names = {b["name"] for b in branches["branches"]}
        assert "feature/demo" in names
        current = [b for b in branches["branches"] if b["current"]]
        assert current[0]["name"] == "feature/demo"

    async def test_mutations_require_capability_and_audit(self, git_env):
        await git_env.svc.init(".")
        await git_env.settings.set("cap_git_operate", "false")
        with pytest.raises(AppError) as ei:
            await git_env.svc.commit(".", "nope")
        assert ei.value.code == "GIT_DISABLED"
        rows = await git_env.executions.list()
        denied = [r for r in rows if r["kind"] == "git:commit"
                  and r["status"] == "denied"]
        assert denied, "denied mutation must be audited"

    async def test_every_git_op_audited(self, git_env):
        await make_repo(git_env.svc)
        kinds = {r["kind"] for r in await git_env.executions.list()}
        assert {"git:init", "git:add", "git:commit"} <= kinds

    async def test_push_to_local_remote_no_token(self, git_env, tmp_path):
        root = await make_repo(git_env.svc, path="repo")
        # a real push — over file:// to a bare remote (no network, no token)
        bare = tmp_path / "remote.git"
        import subprocess
        subprocess.run(["git", "init", "--bare", str(bare)], check=True,
                       capture_output=True)
        rc, _, err = await git_env.svc._run(
            root, ["remote", "add", "origin", str(bare)], op="remote")
        assert rc == 0, err
        res = await git_env.svc.push("repo", "origin")
        assert res["pushed"] is True
        assert res["branch"] in ("main", "master")

    async def test_push_https_without_token_denied(self, git_env):
        root = await make_repo(git_env.svc)
        rc, _, _ = await git_env.svc._run(
            root, ["remote", "add", "origin",
                   "https://github.com/some/repo.git"], op="remote")
        assert rc == 0
        with pytest.raises(AppError) as ei:
            await git_env.svc.push(".", "origin", github_token=None)
        assert ei.value.code == "GIT_NO_TOKEN"

    async def test_push_token_never_leaks_to_other_hosts(self, git_env):
        root = await make_repo(git_env.svc)
        rc, _, _ = await git_env.svc._run(
            root, ["remote", "add", "origin",
                   "https://evil.example/harvest/repo.git"], op="remote")
        assert rc == 0
        with pytest.raises(AppError) as ei:
            await git_env.svc.push(".", "origin", github_token="ghp_secret")
        assert ei.value.code == "GIT_TOKEN_HOST"
        # the token must not appear anywhere in the error
        assert "ghp_secret" not in str(ei.value.message)

    async def test_add_remote_validates_github(self, git_env):
        await make_repo(git_env.svc)
        with pytest.raises(AppError) as ei:
            await git_env.svc.add_remote(".", "https://gitee.com/u/r.git")
        assert ei.value.code == "GIT_BAD_REMOTE_URL"
        out = await git_env.svc.add_remote(".", "https://github.com/u/r.git")
        assert out["configured"] is True
        st = await git_env.svc.status(".")
        assert st["remote"] == "https://github.com/u/r.git"
        # idempotent: re-adding updates the URL instead of failing
        out = await git_env.svc.add_remote(".", "https://github.com/u/other.git")
        assert out["url"] == "https://github.com/u/other.git"
        # local + ssh remotes are fine too
        out = await git_env.svc.add_remote(".", "/tmp/mirror.git", "backup")
        assert out["remote"] == "backup"
        out = await git_env.svc.add_remote(".", "git@github.com:u/r.git", "ssh")
        assert out["remote"] == "ssh"

    async def test_push_unknown_remote(self, git_env):
        await make_repo(git_env.svc)
        with pytest.raises(AppError) as ei:
            await git_env.svc.push(".", "upstream")
        assert ei.value.code == "GIT_NO_REMOTE"


# ── HTTP surface ─────────────────────────────────────────────────────
class TestGitApi:
    async def test_status_not_a_repo(self, api):
        r = await api.client.get("/api/git/status")
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "GIT_NOT_A_REPO"

    async def test_full_http_flow(self, api):
        ok = await api.client.put("/api/settings",
                                  json={"cap_git_operate": True})
        assert ok.status_code == 200
        r = await api.client.post("/api/git/init", json={"path": "repo"})
        assert r.status_code == 201, r.text
        # a file inside that sandboxed repo dir
        ws = api.svc.projects.workspace_root
        (ws / "repo" / "f.txt").write_text("one\n", encoding="utf-8")
        r = await api.client.post("/api/git/commit",
                                  json={"path": "repo", "message": "c1"})
        assert r.status_code == 200, r.text
        assert r.json()["committed"] is True
        r = await api.client.get("/api/git/log", params={"path": "repo"})
        assert r.json()["count"] == 1
        r = await api.client.post("/api/git/branches",
                                  json={"path": "repo", "name": "dev"})
        assert r.status_code == 201

    async def test_commit_blocked_when_capability_off(self, api):
        await api.client.put("/api/settings", json={"cap_git_operate": True})
        r = await api.client.post("/api/git/init", json={"path": "repo"})
        assert r.status_code == 201
        # cap off → init itself is refused (mutation), audited
        await api.client.put("/api/settings", json={"cap_git_operate": False})
        r = await api.client.post("/api/git/commit",
                                  json={"path": "repo", "message": "c"})
        assert r.status_code == 403
        assert r.json()["error"]["code"] == "GIT_DISABLED"
        r = await api.client.post("/api/git/init", json={"path": "other"})
        assert r.status_code == 403

    async def test_github_token_roundtrip_masked(self, api):
        r = await api.client.put("/api/git/github/token",
                                 json={"token": "ghp_testtoken1234567890"})
        assert r.status_code == 200
        assert "ghp_te" in r.json()["masked"] and "1234567890" not in str(
            r.json())
        status = (await api.client.get("/api/git/github/status")).json()
        assert status["configured"] is True
        # the raw token must never leave the API
        assert "ghp_testtoken1234567890" not in str(status)
        r = await api.client.delete("/api/git/github/token")
        assert r.status_code == 200
        status = (await api.client.get("/api/git/github/status")).json()
        assert status["configured"] is False

    async def test_github_calls_need_token(self, api):
        r = await api.client.get("/api/git/github/user")
        assert r.status_code == 403
        assert r.json()["error"]["code"] == "GITHUB_NO_TOKEN"


# ── GitHub REST client (fake transport) ──────────────────────────────
class TestGitHubClient:
    async def test_headers_and_user_mapping(self, monkeypatch):
        seen = {}

        class FakeResp:
            status_code = 200

            def json(self):
                return {"login": "octo", "name": "Octo Cat",
                        "avatar_url": "https://x/a.png",
                        "html_url": "https://github.com/octo"}

        class FakeClient:
            def __init__(self, *a, **kw):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def get(self, url, headers=None, params=None):
                seen["url"] = url
                seen["headers"] = headers
                return FakeResp()

        monkeypatch.setattr("backend.app.gitops.github.httpx.AsyncClient",
                            FakeClient)
        user = await GitHubClient("ghp_test").user()
        assert user["login"] == "octo"
        assert seen["url"] == "https://api.github.com/user"
        assert seen["headers"]["Authorization"] == "Bearer ghp_test"
        assert seen["headers"]["Accept"] == "application/vnd.github+json"

    async def test_401_honest(self, monkeypatch):
        class FakeResp:
            status_code = 401

            def json(self):
                return {"message": "Bad credentials"}

            text = "Bad credentials"

        class FakeClient:
            def __init__(self, *a, **kw):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def get(self, *a, **kw):
                return FakeResp()

        monkeypatch.setattr("backend.app.gitops.github.httpx.AsyncClient",
                            FakeClient)
        with pytest.raises(AppError) as ei:
            await GitHubClient("bad").user()
        assert ei.value.code == "GITHUB_AUTH"
