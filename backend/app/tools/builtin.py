"""Built-in tools (P3) — sandboxed filesystem + allow-listed shell.

Rules that can never regress:
* every path is proven inside the workspace via ``resolve_within``
  (Windows separators, UNC paths and ``..`` escapes all blocked);
* every file mutation returns a real unified diff;
* shell runs execute with cwd inside the sandbox, argv[0] must be on the
  allowlist (and never on the hard BLOCKED_COMMANDS list), arguments are
  scanned for dangerous patterns, and output is truncated deterministically.
"""
from __future__ import annotations

import asyncio
import difflib
import os
import shlex
import time
from pathlib import Path

from ..core.errors import PathEscapeError
from ..security.permissions import Capability, PermissionPolicy
from ..workspace.paths import resolve_within
from .registry import ToolContext, ToolDanger, ToolRegistry, ToolResult, ToolSpec

MAX_READ_BYTES = 60_000
MAX_LIST_ENTRIES = 250
MAX_OUTPUT = 8000
SHELL_DEFAULT_TIMEOUT = 120
SHELL_MAX_TIMEOUT = 300

# argv[0] allowlist (defense-in-depth; BLOCKED_COMMANDS still wins).
ALLOWED_PROGRAMS = {
    "python", "python3", "py", "pytest", "pip", "pip3", "node", "npm", "npx",
    "git", "rg", "ls", "cat", "echo", "type", "dir", "where", "pwd", "cd",
    "ruff", "mypy", "black", "uv",
}

# Argument patterns that make an allowed program destructive.
DANGEROUS_ARG_PATTERNS = [
    "rm -rf", "rm -fr", "rmdir /s", "del /f", "del /s", "format ",
    "mkfs", "shutdown", "reboot", "> /dev/", ":(){", "chmod -r 000",
]


def _truncate(text: str, limit: int = MAX_OUTPUT) -> str:
    if len(text) <= limit:
        return text
    half = limit // 2
    return text[:half] + f"\n…[{len(text) - limit} characters truncated]…\n" + text[-half:]


def _unified_diff(old: str | None, new: str, path: str, max_lines: int = 120) -> str:
    old_lines = (old or "").splitlines(keepends=True)
    diff = list(difflib.unified_diff(
        old_lines, new.splitlines(keepends=True),
        fromfile=f"a/{path}" + ("" if old is not None else " (absent)"),
        tofile=f"b/{path}", lineterm=""))
    if len(diff) > max_lines:
        diff = diff[:max_lines] + [f"\n…[{len(diff) - max_lines} diff lines truncated]\n"]
    return "".join(diff)


# ── filesystem tools ─────────────────────────────────────────────────
async def fs_list(args: dict, ctx: ToolContext) -> ToolResult:
    t0 = time.monotonic()
    target = resolve_within(ctx.root, args.get("path") or ".")
    if not target.exists():
        return ToolResult(ok=False, output="", error=f"Path does not exist: {args.get('path')}")
    rows: list[str] = []
    if target.is_file():
        rows.append(f"{target.name}  ({target.stat().st_size} bytes)")
    else:
        count = 0
        for base, dirs, files in os.walk(target):
            dirs[:] = [d for d in sorted(dirs) if not d.startswith(".")
                       and d not in {"node_modules", "__pycache__", "venv", ".venv"}]
            depth = len(Path(base).relative_to(target).parts)
            if depth > 3:
                dirs[:] = []
                continue
            for d in dirs:
                rows.append("  " * depth + d + "/")
                count += 1
            for f in sorted(files)[:50]:
                if f.startswith("."):
                    continue
                rows.append("  " * depth + f)
                count += 1
            if count >= MAX_LIST_ENTRIES:
                rows.append("…[listing truncated]")
                break
    ms = (time.monotonic() - t0) * 1000
    return ToolResult(ok=True, output="\n".join(rows) or "(empty directory)", ms=round(ms, 1))


async def fs_read(args: dict, ctx: ToolContext) -> ToolResult:
    t0 = time.monotonic()
    target = resolve_within(ctx.root, args["path"])
    if not target.exists() or not target.is_file():
        return ToolResult(ok=False, output="", error=f"File not found: {args['path']}")
    data = target.read_bytes()[:MAX_READ_BYTES + 1]
    truncated = len(data) > MAX_READ_BYTES
    text = data[:MAX_READ_BYTES].decode("utf-8", errors="replace")
    start = max(1, int(args.get("start_line") or 1))
    end = int(args["end_line"]) if args.get("end_line") else None
    lines = text.splitlines()
    if start > 1 or end:
        lines = lines[start - 1:end]
        header = f"[lines {start}–{end or 'end'} of {args['path']}]\n"
    else:
        header = ""
    total_hint = f" ({len(target.read_bytes())} bytes, file truncated to first {MAX_READ_BYTES})" if truncated else ""
    ms = (time.monotonic() - t0) * 1000
    return ToolResult(ok=True,
                      output=header + "\n".join(lines) + total_hint or "(empty file)",
                      ms=round(ms, 1))


async def fs_write(args: dict, ctx: ToolContext) -> ToolResult:
    t0 = time.monotonic()
    target = resolve_within(ctx.root, args["path"])
    content: str = args["content"]
    old = target.read_text(encoding="utf-8", errors="replace") if target.is_file() else None
    target.parent.mkdir(parents=True, exist_ok=True)   # still provably inside root
    target.write_text(content, encoding="utf-8")
    diff = _unified_diff(old, content, args["path"])
    ms = (time.monotonic() - t0) * 1000
    verb = "updated" if old is not None else "created"
    return ToolResult(ok=True, output=f"{verb} {args['path']} ({len(content)} bytes)",
                      diff=diff, ms=round(ms, 1))


async def fs_edit(args: dict, ctx: ToolContext) -> ToolResult:
    t0 = time.monotonic()
    target = resolve_within(ctx.root, args["path"])
    if not target.is_file():
        return ToolResult(ok=False, output="", error=f"File not found: {args['path']}")
    old_text = target.read_text(encoding="utf-8", errors="replace")
    needle: str = args["old_text"]
    count = old_text.count(needle)
    if count == 0:
        return ToolResult(ok=False, output="",
                          error="old_text not found in file — no change made")
    if count > 1:
        return ToolResult(ok=False, output="",
                          error=f"old_text occurs {count}× — make it unique")
    new_text = old_text.replace(needle, args["new_text"], 1)
    target.write_text(new_text, encoding="utf-8")
    diff = _unified_diff(old_text, new_text, args["path"])
    ms = (time.monotonic() - t0) * 1000
    return ToolResult(ok=True, output=f"edited {args['path']}", diff=diff, ms=round(ms, 1))


# ── web tools (P6) ────────────────────────────────────────────────────
async def web_search_tool(args: dict, ctx: ToolContext) -> ToolResult:
    """DuckDuckGo text search; the network layer itself enforces safety."""
    from ..research import web as weblayer  # deferred: optional deps (ddgs)
    t0 = time.monotonic()
    query: str = args["query"]
    max_results = int(args.get("max_results") or 5)
    try:
        results = await weblayer.web_search(query, max_results=max_results)
    except Exception as exc:
        return ToolResult(ok=False, output="",
                          error=f"search failed: {type(exc).__name__}: {exc}")
    if not results:
        return ToolResult(ok=True, output=f'No results for "{query}".',
                          ms=round((time.monotonic() - t0) * 1000, 1))
    lines = [f"{i + 1}. {r.title}\n   {r.url}\n   {r.snippet}"
             for i, r in enumerate(results)]
    ms = (time.monotonic() - t0) * 1000
    return ToolResult(ok=True, ms=round(ms, 1),
                      output=_truncate(f'Search results for "{query}":\n\n'
                                       + "\n\n".join(lines)))


async def web_fetch_tool(args: dict, ctx: ToolContext) -> ToolResult:
    """Fetch one public page (SSRF-guarded) and extract readable text."""
    from ..research import web as weblayer  # deferred: optional deps
    t0 = time.monotonic()
    url: str = args["url"]
    page = await weblayer.web_fetch(url)
    ms = (time.monotonic() - t0) * 1000
    if page.error:
        return ToolResult(ok=False, output="", error=f"{url}: {page.error}",
                          ms=round(ms, 1))
    text = _truncate(page.text, MAX_OUTPUT)
    note = (f"\n[extracted {page.chars} chars"
            + (", truncated" if page.truncated else "") + "]")
    header = f"# {page.title or url}\n{url}\n"
    return ToolResult(ok=True, output=header + text + note, ms=round(ms, 1))


# ── memory tools (P8) ───────────────────────────────────────────────
async def memory_search_tool(args: dict, ctx: ToolContext) -> ToolResult:
    if ctx.memory is None:
        return ToolResult(ok=False, output="",
                          error="memory is not configured for this run")
    t0 = time.monotonic()
    rows = await ctx.memory.search(str(args.get("query") or ""), limit=10)
    ms = (time.monotonic() - t0) * 1000
    if not rows:
        return ToolResult(ok=True, output="No memories found.",
                          ms=round(ms, 1))
    return ToolResult(ok=True, ms=round(ms, 1),
                      output="\n".join(f"- {r['key']}: {r['content']}"
                                       for r in rows))


async def memory_save_tool(args: dict, ctx: ToolContext) -> ToolResult:
    if ctx.memory is None:
        return ToolResult(ok=False, output="",
                          error="memory is not configured for this run")
    t0 = time.monotonic()
    try:
        saved = await ctx.memory.save(args["key"], args["content"],
                                      source=f"agent:{ctx.run_id or 'run'}")
    except ValueError as exc:
        return ToolResult(ok=False, output="", error=str(exc))
    ms = (time.monotonic() - t0) * 1000
    return ToolResult(ok=True, output=f"memory saved: {saved['key']}",
                      ms=round(ms, 1))


async def memory_forget_tool(args: dict, ctx: ToolContext) -> ToolResult:
    if ctx.memory is None:
        return ToolResult(ok=False, output="",
                          error="memory is not configured for this run")
    t0 = time.monotonic()
    removed = await ctx.memory.forget(str(args["key"]))
    ms = (time.monotonic() - t0) * 1000
    if not removed:
        return ToolResult(ok=False, output="",
                          error=f"no memory with key '{args['key']}'")
    return ToolResult(ok=True, output=f"memory removed: {args['key']}",
                      ms=round(ms, 1))


# ── shell tool ───────────────────────────────────────────────────────
def split_command(command: str) -> list[str]:
    return shlex.split(command, posix=(os.name != "nt"))


def check_command_allowed(command: str, policy: PermissionPolicy) -> str | None:
    """Static gate before any process spawn. Returns an error or None."""
    if not command.strip():
        return "empty command"
    try:
        argv = split_command(command)
    except ValueError as exc:
        return f"cannot parse command: {exc}"
    if not argv:
        return "empty command"
    prog = Path(argv[0]).name.lower()
    stem = prog.rsplit(".", 1)[0]
    if policy.command_is_blocked(command):
        return f"program '{prog}' is hard-blocked by security policy"
    if prog not in ALLOWED_PROGRAMS and stem not in ALLOWED_PROGRAMS:
        return (f"program '{prog}' is not on the command allowlist "
                f"({', '.join(sorted(ALLOWED_PROGRAMS))})")
    lowered = command.lower()
    for pattern in DANGEROUS_ARG_PATTERNS:
        if pattern in lowered:
            return f"command contains a dangerous pattern: '{pattern}'"
    for sep in ("&&", "||", ";", "|"):
        if sep in command:
            return f"command chaining ('{sep}') is not allowed — run one command per call"
    return None


async def shell_run(args: dict, ctx: ToolContext) -> ToolResult:
    t0 = time.monotonic()
    command: str = args["command"]
    timeout = min(int(args.get("timeout_s") or SHELL_DEFAULT_TIMEOUT), SHELL_MAX_TIMEOUT)
    try:
        argv = split_command(command)
    except ValueError as exc:
        return ToolResult(ok=False, output="", error=f"cannot parse command: {exc}")
    cwd = ctx.root
    cwd.mkdir(parents=True, exist_ok=True)
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv, cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    except FileNotFoundError:
        return ToolResult(ok=False, output="", error=f"program not found: {argv[0]}")
    except OSError as exc:
        return ToolResult(ok=False, output="", error=f"could not start process: {exc}")
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        text = out.decode("utf-8", errors="replace")
        ms = (time.monotonic() - t0) * 1000
        ok = proc.returncode == 0
        return ToolResult(ok=ok, output=_truncate(text) or "(no output)",
                          exit_code=proc.returncode, ms=round(ms, 1),
                          error=None if ok else f"exit code {proc.returncode}")
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        ms = (time.monotonic() - t0) * 1000
        return ToolResult(ok=False, output="(process killed)",
                          error=f"timed out after {timeout}s", ms=round(ms, 1))


def preview_diff(spec_name: str, args: dict, root) -> str | None:
    """Honest pre-approval preview for file mutations (no mutation yet)."""
    try:
        if spec_name == "fs_write":
            target = resolve_within(root, args.get("path", ""))
            old = target.read_text(encoding="utf-8", errors="replace") if target.is_file() else None
            return _unified_diff(old, args.get("content", ""), args.get("path", "?"))
        if spec_name == "fs_edit":
            target = resolve_within(root, args.get("path", ""))
            old = target.read_text(encoding="utf-8", errors="replace") if target.is_file() else None
            if old is None:
                return None
            count = old.count(args.get("old_text", "\x00"))
            if count != 1:
                return None
            return _unified_diff(old, old.replace(args["old_text"], args.get("new_text", ""), 1),
                                 args.get("path", "?"))
    except Exception:
        return None
    return None


def register_builtin_tools(registry: ToolRegistry) -> None:
    registry.register(ToolSpec(
        name="fs_list",
        description=("List files and folders inside the workspace (read-only). "
                     "Pass a relative path; '.' lists the workspace root."),
        parameters={"type": "object",
                    "properties": {"path": {"type": "string",
                                            "description": "Relative path inside the workspace, default '.'"}},
                    "required": []},
        danger=ToolDanger.READ, capability=Capability.FILESYSTEM_READ, handler=fs_list))
    registry.register(ToolSpec(
        name="fs_read",
        description=("Read a text file inside the workspace (read-only). "
                     "Optionally limit to a line range."),
        parameters={"type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Relative file path"},
                        "start_line": {"type": "integer", "description": "First line (1-based)"},
                        "end_line": {"type": "integer", "description": "Last line (inclusive)"}},
                    "required": ["path"]},
        danger=ToolDanger.READ, capability=Capability.FILESYSTEM_READ, handler=fs_read))
    registry.register(ToolSpec(
        name="fs_write",
        description=("Create or overwrite a file inside the workspace. The full new "
                     "content is required. A diff is shown to the user for approval "
                     "before anything is written."),
        parameters={"type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Relative file path"},
                        "content": {"type": "string", "description": "Complete new file content"}},
                    "required": ["path", "content"]},
        danger=ToolDanger.WRITE, capability=Capability.FILESYSTEM_WRITE, handler=fs_write))
    registry.register(ToolSpec(
        name="fs_edit",
        description=("Surgically edit a file: replace ONE exact occurrence of old_text "
                     "with new_text. A diff is shown to the user for approval first."),
        parameters={"type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Relative file path"},
                        "old_text": {"type": "string", "description": "Exact text to replace (must occur exactly once)"},
                        "new_text": {"type": "string", "description": "Replacement text"}},
                    "required": ["path", "old_text", "new_text"]},
        danger=ToolDanger.WRITE, capability=Capability.FILESYSTEM_WRITE, handler=fs_edit))
    registry.register(ToolSpec(
        name="shell_run",
        description=("Run one allow-listed command inside the workspace (cwd = workspace). "
                     f"Allowed programs: {', '.join(sorted(ALLOWED_PROGRAMS))}. One command "
                     "per call, no chaining. Always requires user approval; output is "
                     f"truncated at {MAX_OUTPUT} chars."),
        parameters={"type": "object",
                    "properties": {
                        "command": {"type": "string", "description": "Single command line"},
                        "timeout_s": {"type": "integer",
                                      "description": f"Timeout seconds (≤ {SHELL_MAX_TIMEOUT}, default {SHELL_DEFAULT_TIMEOUT})"}},
                    "required": ["command"]},
        danger=ToolDanger.EXEC, capability=Capability.COMMAND_EXECUTE, handler=shell_run))
    registry.register(ToolSpec(
        name="web_search",
        description=("Search the public web (DuckDuckGo). Returns titles, URLs and "
                     "snippets. Use web_fetch on a result URL to read the full page."),
        parameters={"type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search query"},
                        "max_results": {"type": "integer",
                                        "description": "Number of results (1–8, default 5)"}},
                    "required": ["query"]},
        danger=ToolDanger.READ, capability=Capability.NETWORK_FETCH,
        handler=web_search_tool))
    registry.register(ToolSpec(
        name="web_fetch",
        description=("Fetch one public http(s) page and extract its readable text. "
                     "Private/internal addresses are refused by the SSRF guard; "
                     "content is size-capped and truncated honestly."),
        parameters={"type": "object",
                    "properties": {"url": {"type": "string",
                                           "description": "Public http(s) URL to fetch"}},
                    "required": ["url"]},
        danger=ToolDanger.READ, capability=Capability.NETWORK_FETCH,
        handler=web_fetch_tool))
    registry.register(ToolSpec(
        name="memory_search",
        description=("Search long-term memory (persistent facts from earlier "
                     "runs and the user). Empty query lists the most recent."),
        parameters={"type": "object",
                    "properties": {"query": {"type": "string",
                                             "description": "Search text (optional)"}},
                    "required": []},
        danger=ToolDanger.READ, capability=Capability.MEMORY,
        handler=memory_search_tool))
    registry.register(ToolSpec(
        name="memory_save",
        description=("Save a durable fact to long-term memory for future runs "
                     "(short key + concise content). Requires human approval."),
        parameters={"type": "object",
                    "properties": {
                        "key": {"type": "string",
                                "description": "Short unique label, e.g. 'project stack'"},
                        "content": {"type": "string",
                                    "description": "The fact to remember (concise)"}},
                    "required": ["key", "content"]},
        danger=ToolDanger.WRITE, capability=Capability.MEMORY,
        handler=memory_save_tool))
    registry.register(ToolSpec(
        name="memory_forget",
        description=("Remove a stale memory by key. Requires human approval."),
        parameters={"type": "object",
                    "properties": {"key": {"type": "string",
                                           "description": "Memory key to remove"}},
                    "required": ["key"]},
        danger=ToolDanger.WRITE, capability=Capability.MEMORY,
        handler=memory_forget_tool))
