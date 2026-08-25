"""Git operations (P7) — every repo lives inside the workspace sandbox.

Security invariants (never regress):
* every repo path is proven inside the workspace via ``resolve_within``;
* git runs as an argv subprocess (no shell), cwd pinned to the repo, with
  ``GIT_TERMINAL_PROMPT=0`` — git can never hang waiting for credentials;
* mutating operations require the ``cap_git_operate`` capability and are
  audited to the ``executions`` table — including denials;
* credentials never appear in argv, audit rows or logs: pushes over HTTPS
  authenticate via a GIT_ASKPASS helper + env var under DATA_DIR;
* remote URLs are redacted (userinfo stripped) before leaving this module;
* destructive commands (reset --hard, clean, force-push, branch -D) are
  simply not offered — the surface is read/init/branch/commit/push only.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import stat
from pathlib import Path

from ..core.errors import AppError, BadRequest
from ..db.repo import ExecutionsRepo
from ..services.settings_service import SettingsService
from ..workspace.paths import resolve_within

log = logging.getLogger("aicc.git")

GIT_TIMEOUT_S = 45.0
MAX_OUT = 400_000
MAX_LOGS = 4000
DIFF_MAX_CHARS = 200_000

_BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/\-]{0,80}$")
_USERINFO_RE = re.compile(r"(https?://)[^/@\s]+@")


def redact_url(url: str) -> str:
    """Strip any userinfo/token from a remote URL before display/audit."""
    return _USERINFO_RE.sub(r"\1***@", (url or "").strip())


def validate_branch_name(name: str) -> str:
    name = (name or "").strip()
    if (not _BRANCH_RE.match(name) or ".." in name or name.endswith(".lock")
            or name.endswith("/") or name == "-"):
        raise BadRequest(
            f"Invalid branch name '{name}'. Letters, digits, '.', '_', '-', "
            "'/' only; no '..', no leading '-'.", code="GIT_BAD_BRANCH")
    return name


class GitService:
    def __init__(self, *, executions: ExecutionsRepo, workspace_root: Path,
                 data_dir: Path, settings: SettingsService,
                 projects=None):
        self.executions = executions
        self.workspace_root = Path(workspace_root).resolve()
        self.data_dir = Path(data_dir)
        self.settings = settings
        self.projects = projects

    # ── path containment (the sandbox boundary) ──────────────────────
    def resolve_repo(self, path: str | None) -> Path:
        rel = (path or ".").strip() or "."
        try:
            return resolve_within(self.workspace_root, rel)
        except Exception as exc:
            raise AppError(getattr(exc, "message", str(exc)),
                           code="PATH_ESCAPE_BLOCKED", status_code=403) from exc

    async def resolve_target(self, path: str | None,
                             project_id: int | None = None) -> Path:
        if project_id is not None:
            if self.projects is None:
                raise BadRequest("Projects are not available.", code="PROJECT_NOT_FOUND")
            _row, root = await self.projects.root_for_id(project_id)
            assert root is not None
            return root
        return self.resolve_repo(path)

    def display_path(self, root: Path) -> str:
        try:
            return str(root.relative_to(self.workspace_root)) or "."
        except ValueError:
            return str(root)

    # ── capability gate (mutating ops) ───────────────────────────────
    async def _require_operate(self, op: str, args: list[str]) -> None:
        if not await self.settings.get_typed("cap_git_operate"):
            await self._audit(op, args, status="denied", rc=None,
                              log="capability 'git:operate' is not granted")
            raise AppError(
                "Git mutations are disabled: the 'git:operate' capability is "
                "off. Enable it in Settings → Agent permissions.",
                code="GIT_DISABLED", status_code=403)

    # ── subprocess runner (argv, no shell) + audit ───────────────────
    async def _run(self, root: Path, args: list[str], *,
                   op: str, timeout: float = GIT_TIMEOUT_S,
                   env_extra: dict[str, str] | None = None,
                   audit: bool = True) -> tuple[int, str, str]:
        env = dict(os.environ)
        env["GIT_TERMINAL_PROMPT"] = "0"     # never block on credential prompts
        if env_extra:
            env.update(env_extra)
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", *args, cwd=str(root),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE, env=env)
            try:
                out_b, err_b = await asyncio.wait_for(proc.communicate(), timeout)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                if audit:
                    await self._audit(op, args, status="error", rc=None,
                                      log=f"timeout after {timeout}s")
                raise AppError(f"git {' '.join(args[:1])} timed out after "
                               f"{int(timeout)}s.", code="GIT_TIMEOUT",
                               status_code=504)
        except FileNotFoundError as exc:
            raise AppError("The 'git' executable was not found on this system. "
                           "Install Git and retry.", code="GIT_MISSING",
                           status_code=503) from exc
        out = out_b.decode("utf-8", errors="replace")[:MAX_OUT]
        err = err_b.decode("utf-8", errors="replace")[:MAX_LOGS]
        rc = proc.returncode if proc.returncode is not None else -1
        if audit:
            await self._audit(op, args, status="ok" if rc == 0 else "error",
                              rc=rc, log=(err or out))
        return rc, out, err

    async def _audit(self, op: str, args: list[str], *, status: str,
                     rc: int | None, log: str) -> None:
        safe_cmd = "git " + " ".join(redact_url(a) for a in args)[:900]
        await self.executions.log(kind=f"git:{op}", status=status,
                                  command=safe_cmd, actor="user", exit_code=rc,
                                  log_text=(log or "")[:MAX_LOGS])

    # ── read operations ──────────────────────────────────────────────
    async def _ensure_repo(self, root: Path) -> None:
        if not root.is_dir():
            raise BadRequest(
                "Directory does not exist inside the workspace.",
                code="GIT_NOT_A_REPO")
        rc, _, _ = await self._run(root, ["rev-parse", "--is-inside-work-tree"],
                                   op="probe", audit=False)
        if rc != 0:
            raise BadRequest(
                f"'{root.relative_to(self.workspace_root)}' is not a git "
                "repository. Initialize it first.", code="GIT_NOT_A_REPO")

    async def status(self, path: str | None, project_id: int | None = None) -> dict:
        root = await self.resolve_target(path, project_id)
        await self._ensure_repo(root)
        rc, out, err = await self._run(
            root, ["status", "--porcelain=v1", "--branch"], op="status")
        if rc != 0:
            raise AppError(f"git status failed: {err.strip()[:300]}",
                           code="GIT_ERROR", status_code=502)
        branch = ""
        ahead = behind = 0
        files: list[dict] = []
        for line in out.splitlines():
            if line.startswith("## "):
                head = line[3:]
                m = re.search(r"\[ahead (\d+)(?:, behind (\d+))?\]", head)
                if m:
                    ahead = int(m.group(1))
                    behind = int(m.group(2) or 0)
                branch = head.split("...")[0].split(" [")[0].strip()
                continue
            if len(line) < 4:
                continue
            xy, fp = line[:2], line[3:]
            if " -> " in fp:                      # rename: "orig -> new"
                fp = fp.split(" -> ", 1)[1]
            files.append({"path": fp, "x": xy[0], "y": xy[1],
                          "staged": xy[0] not in (" ", "?", "!"),
                          "untracked": xy == "??"})
        rc, url, _ = await self._run(root, ["remote", "get-url", "origin"],
                                     op="remote", audit=False)
        remote = redact_url(url) if rc == 0 and url.strip() else None
        return {"path": self.display_path(root),
                "branch": branch or "(no commits yet)", "ahead": ahead,
                "behind": behind, "remote": remote, "files": files,
                "clean": not files}

    async def log(self, path: str | None, limit: int = 20) -> dict:
        root = self.resolve_repo(path)
        await self._ensure_repo(root)
        limit = max(1, min(int(limit), 100))
        rc, out, err = await self._run(
            root, ["log", f"-n{limit}", "--date=short",
                   "--pretty=format:%h%x1f%ad%x1f%an%x1f%d%x1f%s"], op="log")
        if rc != 0:
            # empty repo has no HEAD — an honest empty history
            if "does not have any commits" in err or "bad default revision" in err:
                return {"commits": [], "count": 0}
            raise AppError(f"git log failed: {err.strip()[:300]}",
                           code="GIT_ERROR", status_code=502)
        commits = []
        for line in out.splitlines():
            parts = line.split("\x1f")
            if len(parts) < 5:
                continue
            commits.append({"sha": parts[0], "date": parts[1],
                            "author": parts[2],
                            "decorations": parts[3].strip(" ()"),
                            "message": parts[4][:300]})
        return {"commits": commits, "count": len(commits)}

    async def diff(self, path: str | None, file: str | None = None,
                   staged: bool = False) -> dict:
        root = self.resolve_repo(path)
        await self._ensure_repo(root)
        args = ["diff"] + (["--staged"] if staged else [])
        if file:
            target = resolve_within(root, file)     # proven inside the repo
            args += ["--", str(target.relative_to(root))]
        rc, out, err = await self._run(root, args, op="diff")
        if rc != 0:
            raise AppError(f"git diff failed: {err.strip()[:300]}",
                           code="GIT_ERROR", status_code=502)
        truncated = len(out) > DIFF_MAX_CHARS
        return {"diff": out[:DIFF_MAX_CHARS], "truncated": truncated,
                "file": file, "staged": staged}

    async def branches(self, path: str | None) -> dict:
        root = self.resolve_repo(path)
        await self._ensure_repo(root)
        rc, out, err = await self._run(
            root, ["branch", "-a", "--format=%(refname:short)"], op="branches")
        if rc != 0:
            raise AppError(f"git branch failed: {err.strip()[:300]}",
                           code="GIT_ERROR", status_code=502)
        rc, cur, _ = await self._run(root, ["branch", "--show-current"],
                                     op="probe", audit=False)
        current = cur.strip() if rc == 0 else ""
        branches = [{"name": n.strip(), "current": n.strip() == current}
                    for n in out.splitlines() if n.strip()]
        return {"branches": branches, "count": len(branches)}

    # ── mutations (capability-gated + audited) ───────────────────────
    async def init(self, path: str | None) -> dict:
        root = self.resolve_repo(path)
        root.mkdir(parents=True, exist_ok=True)   # still inside the workspace
        await self._require_operate("init", ["init"])
        rc, out, err = await self._run(root, ["init"], op="init")
        if rc != 0:
            raise AppError(f"git init failed: {err.strip()[:300]}",
                           code="GIT_ERROR", status_code=502)
        rc, branch, _ = await self._run(root, ["branch", "--show-current"],
                                        op="probe", audit=False)
        return {"path": str(root.relative_to(self.workspace_root)),
                "initialized": True,
                "branch": branch.strip() or "main",
                "message": out.strip().splitlines()[-1] if out.strip() else "ok"}

    async def create_branch(self, path: str | None, name: str) -> dict:
        root = self.resolve_repo(path)
        name = validate_branch_name(name)
        await self._require_operate("branch", ["checkout", "-b", name])
        await self._ensure_repo(root)
        rc, out, err = await self._run(root, ["checkout", "-b", name],
                                       op="branch")
        if rc != 0:
            raise AppError(f"git checkout -b failed: {err.strip()[:300]}",
                           code="GIT_ERROR", status_code=502)
        return {"created": True, "branch": name, "switched": True}

    async def commit(self, path: str | None, message: str,
                     files: list[str] | None = None) -> dict:
        root = self.resolve_repo(path)
        message = (message or "").strip()
        if not message:
            raise BadRequest("Commit message must not be empty.",
                             code="GIT_BAD_MESSAGE")
        message = message[:500]
        await self._require_operate("commit", ["commit", "-m", message])
        await self._ensure_repo(root)

        # stage: explicit files (each proven inside the repo) or everything
        if files:
            rels = []
            for f in files:
                target = resolve_within(root, f)
                rels.append(str(target.relative_to(root)))
            rc, _, err = await self._run(root, ["add", "--"] + rels, op="add")
        else:
            rc, _, err = await self._run(root, ["add", "-A"], op="add")
        if rc != 0:
            raise AppError(f"git add failed: {err.strip()[:300]}",
                           code="GIT_ERROR", status_code=502)

        # identity: repo config wins; otherwise an honest, explicit fallback
        rc, name, _ = await self._run(root, ["config", "user.name"],
                                      op="probe", audit=False)
        ident: list[str] = []
        if rc != 0 or not name.strip():
            ident = ["-c", "user.name=AI Command Center",
                     "-c", "user.email=ai-command-center@localhost"]
        rc, out, err = await self._run(
            root, ident + ["commit", "-m", message], op="commit")
        if rc != 0:
            raise AppError(f"git commit failed: {err.strip()[:300]}",
                           code="GIT_ERROR", status_code=502)
        rc, sha, _ = await self._run(root, ["rev-parse", "--short", "HEAD"],
                                     op="probe", audit=False)
        return {"committed": True, "sha": sha.strip(),
                "message": out.strip().splitlines()[-1] if out.strip() else ""}

    # ── push (HTTPS via GIT_ASKPASS; file:// and ssh pass directly) ──
    def _askpass_setup(self, token: str) -> dict[str, str]:
        """Helper script under DATA_DIR; token travels only via env."""
        helper_dir = self.data_dir / "git-askpass"
        helper_dir.mkdir(parents=True, exist_ok=True)
        sh = helper_dir / "askpass.sh"
        bat = helper_dir / "askpass.bat"
        sh.write_text(
            "#!/bin/sh\ncase \"$1\" in\n"
            "  *sername*) echo \"x-access-token\" ;;\n"
            "  *) echo \"$GIT_AICC_TOKEN\" ;;\nesac\n", encoding="utf-8")
        bat.write_text(
            "@echo off\r\n"
            "echo %1|findstr /I /C:\"Username\" >nul\r\n"
            "if %errorlevel%==0 (echo x-access-token) else (echo %GIT_AICC_TOKEN%)\r\n",
            encoding="utf-8")
        try:
            sh.chmod(sh.stat().st_mode | stat.S_IXUSR | stat.S_IRUSR
                     | stat.S_IWUSR)
        except OSError:
            pass
        script = bat if os.name == "nt" else sh
        return {"GIT_ASKPASS": str(script), "GIT_AICC_TOKEN": token}

    async def push(self, path: str | None, remote: str = "origin",
                   github_token: str | None = None,
                   set_upstream: bool = False) -> dict:
        root = self.resolve_repo(path)
        await self._require_operate("push", ["push", remote])
        await self._ensure_repo(root)
        if not re.match(r"^[A-Za-z0-9._\-]{1,60}$", remote or ""):
            raise BadRequest(f"Invalid remote name '{remote}'.",
                             code="GIT_BAD_REMOTE")
        rc, url, _ = await self._run(root, ["remote", "get-url", remote],
                                     op="remote", audit=False)
        if rc != 0:
            raise BadRequest(f"Remote '{remote}' does not exist.",
                             code="GIT_NO_REMOTE")
        url = url.strip()
        env_extra = None
        if url.startswith(("http://", "https://")):
            host = url.split("/", 3)[2] if "://" in url else ""
            if not github_token:
                raise AppError(
                    "HTTPS push needs a GitHub token: set it under "
                    "Git → GitHub → token.", code="GIT_NO_TOKEN",
                    status_code=403)
            if "github.com" not in host.lower():
                raise BadRequest(
                    "Credential safety: the stored GitHub token is only sent to "
                    f"github.com, never to '{host}'.", code="GIT_TOKEN_HOST")
            env_extra = self._askpass_setup(github_token)
        rc, branch, err = await self._run(
            root, ["rev-parse", "--abbrev-ref", "HEAD"], op="probe", audit=False)
        if rc != 0 or branch.strip() == "HEAD":
            raise BadRequest("Cannot push: the repository is in a detached "
                             "HEAD state.", code="GIT_DETACHED")
        branch = branch.strip()
        args = ["push"] + (["-u"] if set_upstream else []) + [remote, branch]
        rc, out, err = await self._run(root, args, op="push",
                                       env_extra=env_extra)
        if rc != 0:
            raise AppError(f"git push failed: {redact_url(err).strip()[:400]}",
                           code="GIT_PUSH_FAILED", status_code=502)
        return {"pushed": True, "remote": remote, "branch": branch,
                "remote_url": redact_url(url),
                "message": redact_url((err or out)).strip()[:400]}

    async def add_remote(self, path: str | None, url: str,
                         remote: str = "origin") -> dict:
        """Attach a GitHub repo as a remote (capability-gated, audited)."""
        root = self.resolve_repo(path)
        await self._require_operate("remote", ["remote", "add", remote])
        await self._ensure_repo(root)
        url = (url or "").strip()
        github_https = re.match(
            r"^https://github\.com/[\w.\-]+/[\w.\-]+(?:\.git)?/?$", url)
        github_ssh = re.match(r"^git@github\.com:[\w.\-]+/[\w.\-]+(?:\.git)?$",
                              url)
        local = (url.startswith("file://") or url.startswith("/")
                 or (os.name == "nt" and re.match(r"^[A-Za-z]:[\\/]", url)))
        other_https = url.startswith(("http://", "https://")) and not github_https
        if other_https:
            raise BadRequest(
                "Only github.com remote URLs are accepted over http(s) — the "
                "sandbox never proxies credentials to other hosts. Local "
                "file:// and git@github.com remotes are fine.",
                code="GIT_BAD_REMOTE_URL")
        if not (github_https or github_ssh or local):
            raise BadRequest(
                f"Remote URL '{url[:80]}' not understood. Use "
                "https://github.com/<owner>/<repo>, git@github.com:…, or a "
                "local file path.", code="GIT_BAD_REMOTE_URL")
        if not re.match(r"^[A-Za-z0-9._\-]{1,60}$", remote or ""):
            raise BadRequest(f"Invalid remote name '{remote}'.",
                             code="GIT_BAD_REMOTE")
        rc, _, err = await self._run(
            root, ["remote", "add", remote, url], op="remote")
        if rc != 0:
            if "already exists" in err:
                rc, _, err = await self._run(
                    root, ["remote", "set-url", remote, url], op="remote")
            if rc != 0:
                raise AppError(f"git remote add failed: {err.strip()[:300]}",
                               code="GIT_ERROR", status_code=502)
        return {"remote": remote, "url": redact_url(url), "configured": True}
