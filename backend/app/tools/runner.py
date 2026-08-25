"""Controlled command runner for the Agent engine.

Security model (deliberately strict — never unrestricted access):

* allowlist of executables only; no shell is ever used (argv passed to
  ``create_subprocess_exec`` — shell metacharacters are inert and also
  rejected up front);
* destructive / network-ish / interactive commands are blocked;
* arguments may not reference absolute paths, ``..`` escapes, env vars or
  shell substitutions;
* hard timeout (default 120s) kills the process tree;
* working directory is always the sandbox workspace root;
* every invocation is logged to the ``executions`` audit table.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import shlex
import shutil
import sys
from pathlib import Path
from typing import Any

from ..core.errors import BadRequest
from ..db.repo import ExecutionsRepo
from ..security.permissions import BLOCKED_COMMANDS
from .audit import log_execution

log = logging.getLogger("aicc.tools.runner")

# Executables an agent may invoke
ALLOWED_COMMANDS: set[str] = {
    "python", "python3", "pytest", "node", "npm", "tsc",
    "eslint", "ruff", "mypy", "black", "flake8",
}
# npm subcommands we accept. `run`/`test`/`start` execute only scripts
# declared in the project's own package.json inside the sandbox.
# `install`, `exec`, `npx`-style subcommands are intentionally blocked:
# they can download and execute arbitrary code from the network.
ALLOWED_NPM_SUBCOMMANDS = {"run", "test", "start"}
# python invocations: allow scripts/modules, block `-c`/`-e` arbitrary code
BLOCKED_PYTHON_FLAGS = {"-c", "-cmd", "-e", "-exec"}

SHELL_META = re.compile(r"[;&|<>`$\\{}\n\r\t]")
REJECTED_ARG_PREFIXES = ("/", "~", "\\\\")
REJECTED_TRAILING_NAMES = {".", "..", "..\\", "../"}
MAX_OUTPUT_CHARS = 20_000
DEFAULT_TIMEOUT = 120.0
MAX_TIMEOUT = 300.0

# command names that are handled before allowlist lookup (windows variants)
_ALIASES = {"py": "python", "python.exe": "python", "py.exe": "python",
            "pytest.exe": "pytest", "node.exe": "node", "npm.exe": "npm",
            "tsc.exe": "tsc", "git.exe": "git"}


class CommandRunner:
    def __init__(self, workspace: Path, executions: ExecutionsRepo,
                 actor: str = "agent", timeout: float = DEFAULT_TIMEOUT):
        self.workspace = Path(workspace).resolve()
        self.executions = executions
        self.actor = actor
        self.timeout = max(1.0, min(timeout, MAX_TIMEOUT))

    # ── validation ───────────────────────────────────────────────────
    def validate(self, command: str) -> list[str]:
        """Return argv or raise BadRequest. Never returns a shell string."""
        if not command or not command.strip():
            raise BadRequest("Command must not be empty.", code="COMMAND_EMPTY")
        if SHELL_META.search(command):
            raise BadRequest(
                "Command contains shell metacharacters and was blocked.",
                code="SHELL_META_BLOCKED")
        try:
            argv = shlex.split(command, posix=True)
        except ValueError as exc:
            raise BadRequest(f"Command could not be parsed: {exc}",
                             code="COMMAND_PARSE_FAILED") from exc
        if not argv:
            raise BadRequest("Command must not be empty.", code="COMMAND_EMPTY")

        name = _ALIASES.get(argv[0].lower(), argv[0].lower())
        base = os.path.basename(name).lower()
        if base in {b.lower() for b in BLOCKED_COMMANDS}:
            raise BadRequest(f"Command '{base}' is blocked by policy.",
                             code="COMMAND_BLOCKED")
        if base in {"bash", "sh", "cmd", "cmd.exe", "powershell", "pwsh", "pwsh.exe",
                    "curl", "wget", "pip", "pip3", "pipx", "sudo", "su", "env",
                    "ssh", "scp", "sftp", "git", "apt", "apt-get", "yum", "dnf",
                    "winget", "choco", "brew", "systemctl", "docker", "podman",
                    "kill", "taskkill", "timeout", "del", "format", "reg", "reg.exe"}:
            raise BadRequest(f"Command '{base}' is not on the allowlist.",
                             code="COMMAND_NOT_ALLOWED")
        if base not in ALLOWED_COMMANDS:
            raise BadRequest(
                f"Command '{base}' is not on the allowlist. Allowed: "
                f"{', '.join(sorted(ALLOWED_COMMANDS))}.",
                code="COMMAND_NOT_ALLOWED")

        # npm: only run/test/start/exec
        if base == "npm" and len(argv) > 1 and argv[1].lower() not in ALLOWED_NPM_SUBCOMMANDS:
            raise BadRequest(
                f"npm subcommand '{argv[1]}' is blocked (only "
                f"{', '.join(sorted(ALLOWED_NPM_SUBCOMMANDS))} are allowed).",
                code="COMMAND_NOT_ALLOWED")

        # python: block direct code execution flags
        if base in ("python", "python3"):
            for flag in argv[1:]:
                if flag.lower() in BLOCKED_PYTHON_FLAGS:
                    raise BadRequest(
                        f"python flag '{flag}' is blocked (no arbitrary code "
                        "execution through the agent).",
                        code="COMMAND_NOT_ALLOWED")

        for arg in argv[1:]:
            self._validate_arg(arg)
        return argv

    def _validate_arg(self, arg: str) -> None:
        norm = arg.replace("\\", "/")
        if re.match(r"^[A-Za-z]:", norm):  # Windows drive-absolute (C:\...)
            raise BadRequest(f"Argument '{arg}' is an absolute path and was "
                             "blocked.", code="ARG_ESCAPE_BLOCKED")
        if norm.startswith(REJECTED_ARG_PREFIXES):
            raise BadRequest(f"Argument '{arg}' resolves outside the workspace "
                             "and was blocked.", code="ARG_ESCAPE_BLOCKED")
        parts = norm.split("/")
        if any(p in REJECTED_TRAILING_NAMES for p in parts):
            raise BadRequest(f"Argument '{arg}' escapes the workspace and was "
                             "blocked.", code="ARG_ESCAPE_BLOCKED")
        if "$" in arg or "`" in arg:
            raise BadRequest(f"Argument '{arg}' contains substitutions and was "
                             "blocked.", code="ARG_SUBSTITUTION_BLOCKED")

    @staticmethod
    def _resolve_executable(name: str) -> str:
        if name in ("python", "python3"):
            return sys.executable
        found = shutil.which(name)
        if found:
            return found
        found = shutil.which(name + ".exe")  # Windows
        if found:
            return found
        raise BadRequest(f"Executable '{name}' was not found on PATH.",
                         code="EXECUTABLE_NOT_FOUND")

    # ── execution ────────────────────────────────────────────────────
    async def run(self, command: str, timeout: float | None = None) -> dict[str, Any]:
        argv = self.validate(command)
        limit = min(self.timeout, timeout or self.timeout)
        executable = self._resolve_executable(_ALIASES.get(argv[0].lower(), argv[0].lower()))
        full_argv = [executable] + argv[1:]

        env = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": os.environ.get("HOME") or os.environ.get("USERPROFILE", ""),
            "USERPROFILE": os.environ.get("USERPROFILE", ""),
            "SYSTEMROOT": os.environ.get("SYSTEMROOT", "C:\\Windows"),
            "VIRTUAL_ENV": os.environ.get("VIRTUAL_ENV", ""),
            "LANG": os.environ.get("LANG", "en_US.UTF-8"),
            "TERM": "dumb",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        proc = await asyncio.create_subprocess_exec(
            *full_argv,
            cwd=str(self.workspace),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(), timeout=limit)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:  # pragma: no cover
                pass
            try:
                await asyncio.wait_for(proc.communicate(), timeout=5)
            except asyncio.TimeoutError:  # pragma: no cover
                pass
            detail = f"Command timed out after {limit:.0f}s and was killed."
            await self._audit(command, "timeout", detail)
            return {"ok": False, "timed_out": True, "timeout_s": limit,
                    "exit_code": None, "stdout": "", "stderr": detail}

        stdout = stdout_b.decode("utf-8", errors="replace")
        stderr = stderr_b.decode("utf-8", errors="replace")
        if len(stdout) > MAX_OUTPUT_CHARS:
            stdout = stdout[:MAX_OUTPUT_CHARS] + "\n… [output truncated]"
        if len(stderr) > MAX_OUTPUT_CHARS:
            stderr = stderr[:MAX_OUTPUT_CHARS] + "\n… [output truncated]"
        ok = proc.returncode == 0
        status = "success" if ok else "error"
        await self._audit(command, status,
                          f"exit={proc.returncode} out={len(stdout)} err={len(stderr)}")
        return {"ok": ok, "timed_out": False, "timeout_s": limit,
                "exit_code": proc.returncode, "stdout": stdout, "stderr": stderr}

    async def _audit(self, command: str, status: str, detail: str) -> None:
        try:
            await log_execution(self.executions, kind="command", status=status,
                                command=command[:500], actor=self.actor,
                                exit_code=0 if status == "success" else 1,
                                log_text=detail[:2000])
        except Exception:  # pragma: no cover
            log.exception("audit log failed")

    # ── convenience checks (tests / lint / typecheck) ─────────────────
    @staticmethod
    def detect_checks(workspace: Path) -> dict[str, dict[str, str]]:
        """Detect which verification commands apply to a workspace. Never runs anything."""
        checks: dict[str, dict[str, str]] = {}
        if (workspace / "pyproject.toml").exists() or \
           any(workspace.glob("pytest.ini")) or \
           (workspace / "tests").is_dir() or \
           list(workspace.glob("test_*.py")) or list(workspace.glob("*_test.py")):
            checks["tests"] = {"cmd": "pytest -q", "desc": "Python test suite"}
        if (workspace / "package.json").exists():
            checks["typecheck"] = {"cmd": "npm run typecheck",
                                   "desc": "TypeScript type check"}
            checks["tests"] = checks.get("tests") or {"cmd": "npm run test --if-present",
                                                      "desc": "Node test suite"}
            checks["lint"] = {"cmd": "npm run lint --if-present", "desc": "Linter"}
        if list(workspace.glob("*.ts")) or (workspace / "src").is_dir():
            checks.setdefault("typecheck", {"cmd": "tsc --noEmit",
                                            "desc": "TypeScript type check"})
        return checks
