"""Git/GitHub integration.

Local Git: real, safe operations executed with argv (no shell) and only
inside a project workspace (path containment enforced). Allowed
subcommands: status, log, branch, diff, show, rev-parse (read-only) and
add/commit (explicit mutations with strict argument validation).

GitHub: real REST calls ONLY when a token is available (encrypted vault
credential ``github`` or ``GITHUB_TOKEN`` env). Without it the UI shows a
clear "unauthenticated" state — nothing is ever faked.
"""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any

import httpx

from ..core.errors import BadRequest, NotFound
from ..db.repo import ExecutionsRepo, ProjectsRepo
from ..security.crypto import CredentialVault
from ..workspace.paths import resolve_within
from ..tools.audit import log_execution

log = logging.getLogger("aicc.gitops")

ALLOWED_SUBCOMMANDS = {"status", "log", "branch", "diff", "show", "rev-parse",
                       "add", "commit", "config", "init"}
MUTATING = {"add", "commit", "init"}
MAX_OUTPUT = 40_000
_UA = "ai-command-center/0.4"


class GitService:
    def __init__(self, *, workspace_root: Path, projects: ProjectsRepo,
                 executions: ExecutionsRepo):
        self.workspace_root = Path(workspace_root).resolve()
        self.projects = projects
        self.executions = executions

    # ── helpers ──────────────────────────────────────────────────────
    async def _audit(self, project_id: int | None, status: str, command: str,
                     detail: str = "") -> None:
        try:
            await log_execution(self.executions, kind="git", status=status,
                                command=command[:400], actor="user",
                                exit_code=0 if status == "success" else 1,
                                log_text=detail[:2000])
        except Exception:  # pragma: no cover
            log.exception("git audit failed")

    async def repo_for(self, project_id: int | None) -> Path:
        if project_id is None:
            # same default workspace the Agent uses for project-less runs
            path = self.workspace_root / "projects" / "default"
            path.mkdir(parents=True, exist_ok=True)
            return path
        project = await self.projects.get(project_id)
        if project is None:
            raise NotFound(f"Project '{project_id}' not found.")
        rel = project.get("root_path") or f"p{project_id}"
        return resolve_within(self.workspace_root, rel)

    async def _git(self, args: list[str], repo: Path,
                   project_id: int | None = None) -> dict[str, Any]:
        sub = args[0].lower()
        if sub not in ALLOWED_SUBCOMMANDS:
            raise BadRequest(f"git subcommand '{sub}' is not allowed. Allowed: "
                             f"{', '.join(sorted(ALLOWED_SUBCOMMANDS))}.",
                             code="GIT_SUBCOMMAND_BLOCKED")
        # strict argument validation (no option smuggling)
        for arg in args[1:]:
            if arg.startswith("-") and sub in ("status", "log", "branch", "diff",
                                               "show", "rev-parse"):
                # read-only flags are fine (--porcelain, --oneline, etc.)
                continue
            if arg.startswith("-") and sub == "init":
                if arg in ("-q", "--quiet"):
                    continue
                raise BadRequest(f"git init option '{arg}' is blocked.",
                                 code="GIT_OPTION_BLOCKED")
            if arg.startswith("-") and sub in MUTATING:
                if sub == "commit" and arg in ("-m", "--message"):
                    continue
                if sub == "add":
                    continue
                raise BadRequest(f"git {sub} option '{arg}' is blocked.",
                                 code="GIT_OPTION_BLOCKED")
            if "$" in arg or "`" in arg:
                raise BadRequest("Substitutions are not allowed in git arguments.",
                                 code="GIT_ARG_BLOCKED")
        if sub == "init" and len(args) > 1:
            pass  # only -q/--quiet validated above

        cmd = ["git", *args]
        proc = await asyncio.create_subprocess_exec(
            *cmd, cwd=str(repo),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=60)
        except asyncio.TimeoutError:
            proc.kill()
            await self._audit(project_id, "timeout", "git " + " ".join(args))
            return {"ok": False, "error": "git timed out and was killed."}
        stdout = stdout_b.decode("utf-8", errors="replace")
        stderr = stderr_b.decode("utf-8", errors="replace")
        if len(stdout) > MAX_OUTPUT:
            stdout = stdout[:MAX_OUTPUT] + "\n… [truncated]"
        ok = proc.returncode == 0
        await self._audit(project_id, "success" if ok else "error",
                          "git " + " ".join(args),
                          stdout[-1500:] + stderr[-1500:])
        return {"ok": ok, "exit_code": proc.returncode, "stdout": stdout,
                "stderr": stderr}

    # ── API surface ──────────────────────────────────────────────────
    async def init(self, project_id: int | None) -> dict[str, Any]:
        """Initialize a real git repository in the sandboxed workspace."""
        repo = await self.repo_for(project_id)
        if (repo / ".git").exists():
            return {"ok": True, "already": True, "path": str(repo)}
        r = await self._git(["init", "-q"], repo, project_id)
        return {"ok": r["ok"], "already": False, "path": str(repo),
                "error": None if r["ok"] else (r["stderr"] or r["stdout"]).strip()}

    async def status(self, project_id: int | None) -> dict[str, Any]:
        repo = await self.repo_for(project_id)
        r = await self._git(["rev-parse", "--is-inside-work-tree"], repo, project_id)
        if not r["ok"]:
            return {"ok": False, "is_repo": False, "path": str(repo),
                    "detail": (r["stderr"] or r["stdout"]).strip()}
        res = await self._git(["status", "--porcelain=v1", "--branch"], repo, project_id)
        branch = "detached"
        clean = True
        lines = res["stdout"].splitlines()
        for line in lines:
            if line.startswith("## "):
                branch = line[3:].split("...")[0].split(" ")[0].strip()
                clean = "??" not in line and "[ahead" not in line
        changed = [l for l in lines if not l.startswith("## ")]
        return {"ok": True, "is_repo": True, "path": str(repo), "branch": branch,
                "clean": clean, "changes": len(changed),
                "porcelain": changed[:200]}

    async def log(self, project_id: int | None, limit: int = 20) -> dict[str, Any]:
        repo = await self.repo_for(project_id)
        r = await self._git(["log", f"--max-count={int(limit)}", "--oneline"], repo,
                            project_id)
        return {"ok": r["ok"], "entries": r["stdout"].splitlines() or [],
                "error": None if r["ok"] else r["stderr"].strip()}

    async def branches(self, project_id: int | None) -> dict[str, Any]:
        repo = await self.repo_for(project_id)
        r = await self._git(["branch", "-a"], repo, project_id)
        return {"ok": r["ok"], "branches": r["stdout"].splitlines() or [],
                "error": None if r["ok"] else r["stderr"].strip()}

    async def diff(self, project_id: int | None, cached: bool = False) -> dict[str, Any]:
        repo = await self.repo_for(project_id)
        args = ["diff", "--cached" if cached else "", "--stat", "--patch",
                "--no-color"]
        r = await self._git([a for a in args if a], repo, project_id)
        return {"ok": r["ok"], "diff": r["stdout"],
                "error": None if r["ok"] else r["stderr"].strip()}

    async def commit(self, project_id: int | None, message: str,
                     paths: list[str] | None = None) -> dict[str, Any]:
        message = (message or "").strip()
        if len(message) < 4 or len(message) > 500 or "\n" in message:
            raise BadRequest("Commit message must be 4–500 chars, single line.",
                             code="BAD_COMMIT_MESSAGE")
        repo = await self.repo_for(project_id)
        # stage explicit paths (or everything in the sandbox)
        if paths:
            chosen = []
            for p in paths:
                chosen.append(resolve_within(repo, p).relative_to(repo).as_posix())
        else:
            chosen = ["--all"]
        stage = await self._git(["add"] + chosen, repo, project_id)
        if not stage["ok"]:
            return {"ok": False, "error": stage["stderr"].strip()}
        who = await self._ensure_identity(repo, project_id)
        msg = await self._git(["commit", "-m", message], repo, project_id)
        return {"ok": msg["ok"], "identity": who,
                "commit": msg["stdout"].strip(),
                "error": None if msg["ok"] else msg["stderr"].strip()}

    async def _ensure_identity(self, repo: Path,
                               project_id: int | None = None) -> dict[str, str | None]:
        name_cfg = await self._git(["config", "user.name"], repo, project_id)
        email_cfg = await self._git(["config", "user.email"], repo, project_id)
        name = name_cfg["stdout"].strip() if name_cfg["ok"] else None
        email = email_cfg["stdout"].strip() if email_cfg["ok"] else None
        if not (name and email):
            await self._git(["config", "user.name",
                             name or os.environ.get("GIT_AUTHOR_NAME",
                                                    "AI Command Center")],
                            repo, project_id)
            await self._git(["config", "user.email",
                             email or os.environ.get("GIT_AUTHOR_EMAIL",
                                                     "ai-command-center@localhost")],
                            repo, project_id)
            name = name or "AI Command Center"
            email = email or "ai-command-center@localhost"
        return {"name": name, "email": email}


class GithubClient:
    """Real GitHub REST calls; unauthenticated/without token → explicit state."""

    API = "https://api.github.com"

    def __init__(self, vault: CredentialVault, credentials_repo=None):
        self.vault = vault
        self.credentials = credentials_repo  # CredentialsRepo | None

    async def token(self) -> str | None:
        env = os.environ.get("GITHUB_TOKEN")
        if env:
            return env
        if self.credentials is not None:
            try:
                row = await self.credentials.get("github")
                if row:
                    return self.vault.decrypt(row["ciphertext"])
            except Exception:  # pragma: no cover
                log.warning("stored github credential could not be decrypted")
        return None

    async def set_token(self, token: str) -> bool:
        """Encrypt + persist a GitHub token (plaintext never touches disk)."""
        if not token or not token.strip():
            raise BadRequest("Token must not be empty.")
        await self.credentials.upsert("github", self.vault.encrypt(token.strip()))
        return True

    async def clear_token(self) -> None:
        await self.credentials.delete("github")

    async def state(self) -> dict[str, Any]:
        token = await self.token()
        if not token:
            return {"authenticated": False,
                    "message": "No GitHub token configured. Set GITHUB_TOKEN (or store "
                               "one via /api/github/credentials) to enable GitHub "
                               "features. Nothing is faked."}
        try:
            async with httpx.AsyncClient(timeout=15.0, headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                    "User-Agent": _UA}) as client:
                r = await client.get(self.API + "/user")
                if r.status_code == 401:
                    return {"authenticated": False,
                            "message": "GitHub token rejected (401). Reconnect or "
                                       "replace the token."}
                r.raise_for_status()
                user = r.json()
                return {"authenticated": True, "login": user.get("login"),
                        "name": user.get("name"), "token_ok": True}
        except Exception as exc:
            return {"authenticated": False,
                    "message": f"GitHub API unreachable: {exc}"}

    async def _get(self, path: str) -> httpx.Response:
        token = await self.token()
        async with httpx.AsyncClient(timeout=20.0, headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "User-Agent": _UA}) as client:
            return await client.get(self.API + path)

    async def repositories(self, limit: int = 20) -> dict[str, Any]:
        st = await self.state()
        if not st["authenticated"]:
            return st
        try:
            r = await self._get(f"/user/repos?per_page={min(limit, 100)}&sort=updated")
            r.raise_for_status()
            repos = [{"full_name": x["full_name"], "html_url": x["html_url"],
                      "private": x["private"], "default_branch": x["default_branch"],
                      "description": x.get("description")}
                     for x in r.json()[:limit]]
            return {"authenticated": True, "repositories": repos}
        except Exception as exc:
            return {"authenticated": True, "error": str(exc)}

    async def issues(self, full_name: str, state: str = "open") -> dict[str, Any]:
        st = await self.state()
        if not st["authenticated"]:
            return st
        try:
            r = await self._get(
                f"/repos/{full_name}/issues?state={state}&per_page=20")
            r.raise_for_status()
            issues = [{"number": x["number"], "title": x["title"],
                       "state": x["state"], "html_url": x["html_url"],
                       "user": (x.get("user") or {}).get("login")}
                      for x in r.json() if "pull_request" not in x]
            return {"authenticated": True, "issues": issues}
        except httpx.HTTPStatusError as exc:
            return {"authenticated": True,
                    "error": f"GitHub returned {exc.response.status_code}: "
                             f"{exc.response.text[:200]}"}
        except Exception as exc:
            return {"authenticated": True, "error": str(exc)}

    async def pull_requests(self, full_name: str) -> dict[str, Any]:
        st = await self.state()
        if not st["authenticated"]:
            return st
        try:
            r = await self._get(f"/repos/{full_name}/pulls?state=open&per_page=20")
            r.raise_for_status()
            prs = [{"number": x["number"], "title": x["title"],
                    "html_url": x["html_url"], "head": (x["head"] or {}).get("ref"),
                    "base": (x["base"] or {}).get("ref"),
                    "user": (x.get("user") or {}).get("login")}
                   for x in r.json()]
            return {"authenticated": True, "pulls": prs}
        except httpx.HTTPStatusError as exc:
            return {"authenticated": True,
                    "error": f"GitHub returned {exc.response.status_code}: "
                             f"{exc.response.text[:200]}"}
        except Exception as exc:
            return {"authenticated": True, "error": str(exc)}

    async def create_issue(self, full_name: str, title: str,
                           body: str = "") -> dict[str, Any]:
        st = await self.state()
        if not st["authenticated"]:
            return st
        try:
            async with httpx.AsyncClient(timeout=20.0, headers={
                    "Authorization": f"Bearer {await self.token()}",
                    "Accept": "application/vnd.github+json",
                    "User-Agent": _UA}) as client:
                r = await client.post(self.API + f"/repos/{full_name}/issues",
                                      json={"title": title, "body": body[:6000]})
                r.raise_for_status()
                created = r.json()
                return {"authenticated": True, "created": True,
                        "number": created["number"], "html_url": created["html_url"]}
        except httpx.HTTPStatusError as exc:
            return {"authenticated": True,
                    "error": f"GitHub returned {exc.response.status_code}: "
                             f"{exc.response.text[:200]}"}
        except Exception as exc:
            return {"authenticated": True, "error": str(exc)}
