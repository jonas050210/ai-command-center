"""Sandboxed filesystem tools for the Agent engine.

Every path is resolved against a workspace root with ``workspace.resolve_within``
(blocks absolute paths, ``..``/``..\\`` traversal and symlink escapes on every
platform). All operations are logged to the ``executions`` audit table.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from ..core.errors import BadRequest, PathEscapeError
from ..db.repo import ExecutionsRepo
from ..workspace.paths import resolve_within
from .audit import log_execution

log = logging.getLogger("aicc.tools.files")

MAX_READ_BYTES = 200_000
MAX_RESULT_CHARS = 6_000
MAX_SEARCH_MATCHES = 100


class FileToolbox:
    def __init__(self, root: Path, executions: ExecutionsRepo, actor: str = "agent"):
        self.root = Path(root).resolve()
        self.executions = executions
        self.actor = actor

    # ── helpers ──────────────────────────────────────────────────────
    def _resolve(self, rel: str) -> Path:
        if not rel or not rel.strip():
            raise BadRequest("A path argument is required.", code="BAD_PATH")
        return resolve_within(self.root, rel)

    def _resolve_checked(self, rel: str) -> tuple[Path | None, str | None]:
        try:
            return self._resolve(rel), None
        except (PathEscapeError, BadRequest) as exc:
            return None, str(exc)

    async def _audit(self, tool: str, status: str, target: str | None,
                     detail: str = "") -> None:
        try:
            await log_execution(self.executions, kind=f"tool:{tool}", status=status,
                                command=target, actor=self.actor, log_text=detail[:2000])
        except Exception:  # pragma: no cover — audit must never break a tool
            log.exception("audit log failed")

    @staticmethod
    def _truncate(text: str) -> str:
        if len(text) <= MAX_RESULT_CHARS:
            return text
        return text[:MAX_RESULT_CHARS] + f"\n… [truncated, {len(text)} chars total]"

    # ── tools ────────────────────────────────────────────────────────
    async def read_file(self, path: str) -> dict[str, Any]:
        target, err = self._resolve_checked(path)
        if err:
            await self._audit("read_file", "error", path, err)
            return {"ok": False, "error": err}
        try:
            if not target.is_file():
                raise FileNotFoundError(f"not a file: {path}")
            if target.stat().st_size > MAX_READ_BYTES:
                raise BadRequest(
                    f"File too large ({target.stat().st_size} bytes > {MAX_READ_BYTES}).",
                    code="FILE_TOO_LARGE")
            content = target.read_text(encoding="utf-8", errors="replace")
            await self._audit("read_file", "success", path)
            return {"ok": True, "path": str(target.relative_to(self.root)),
                    "content": self._truncate(content)}
        except (PathEscapeError, FileNotFoundError, BadRequest) as exc:
            await self._audit("read_file", "error", path, str(exc))
            return {"ok": False, "error": str(exc)}
        except OSError as exc:
            await self._audit("read_file", "error", path, str(exc))
            return {"ok": False, "error": f"read failed: {exc}"}

    async def write_file(self, path: str, content: str) -> dict[str, Any]:
        target, err = self._resolve_checked(path)
        if err:
            await self._audit("write_file", "error", path, err)
            return {"ok": False, "error": err}
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            await self._audit("write_file", "success", path,
                              f"{len(content)} chars")
            return {"ok": True, "path": str(target.relative_to(self.root)),
                    "bytes": len(content.encode("utf-8"))}
        except (PathEscapeError, OSError) as exc:
            await self._audit("write_file", "error", path, str(exc))
            return {"ok": False, "error": f"write failed: {exc}"}

    async def edit_file(self, path: str, old: str, new: str,
                        replace_all: bool = False) -> dict[str, Any]:
        target, err = self._resolve_checked(path)
        if err:
            await self._audit("edit_file", "error", path, err)
            return {"ok": False, "error": err}
        try:
            if not target.is_file():
                raise FileNotFoundError(f"not a file: {path}")
            text = target.read_text(encoding="utf-8", errors="replace")
            count = text.count(old)
            if count == 0:
                raise ValueError("old text not found in file")
            if not replace_all and count > 1:
                raise ValueError(
                    f"old text matches {count} times — provide unique context "
                    "or set replace_all")
            updated = text.replace(old, new) if replace_all else text.replace(old, new, 1)
            target.write_text(updated, encoding="utf-8")
            await self._audit("edit_file", "success", path,
                              f"{count} replacement(s)")
            return {"ok": True, "path": str(target.relative_to(self.root)),
                    "replacements": count}
        except (PathEscapeError, FileNotFoundError, ValueError, OSError) as exc:
            await self._audit("edit_file", "error", path, str(exc))
            return {"ok": False, "error": str(exc)}

    async def search_files(self, pattern: str, path: str = ".") -> dict[str, Any]:
        base, err = self._resolve_checked(path)
        if err:
            await self._audit("search_files", "error", path, err)
            return {"ok": False, "error": err}
        if not base.is_dir():
            return {"ok": False, "error": f"not a directory: {path}"}
        matches: list[dict[str, Any]] = []
        try:
            for p in base.rglob("*"):
                if not p.is_file():
                    continue
                try:
                    rel = p.relative_to(self.root)
                except ValueError:
                    continue
                if any(part in (".git", "node_modules", ".venv", "dist", "__pycache__",
                                ".pytest_cache", "data", "build")
                       for part in rel.parts):
                    continue
                try:
                    if p.stat().st_size > MAX_READ_BYTES:
                        continue
                    text = p.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                if pattern in text:
                    lines = [ln.strip() for ln in text.splitlines() if pattern in ln][:3]
                    matches.append({"path": str(rel), "lines": lines})
                    if len(matches) >= MAX_SEARCH_MATCHES:
                        break
            await self._audit("search_files", "success", path,
                              f"pattern={pattern!r} matches={len(matches)}")
            return {"ok": True, "pattern": pattern, "matches": matches,
                    "count": len(matches), "truncated": len(matches) >= MAX_SEARCH_MATCHES}
        except (PathEscapeError, OSError) as exc:
            await self._audit("search_files", "error", path, str(exc))
            return {"ok": False, "error": str(exc)}

    async def list_files(self, path: str = ".", max_depth: int = 4) -> dict[str, Any]:
        base, err = self._resolve_checked(path)
        if err:
            await self._audit("list_files", "error", path, err)
            return {"ok": False, "error": err}
        if not base.is_dir():
            return {"ok": False, "error": f"not a directory: {path}"}
        lines: list[str] = []

        def walk(directory: Path, depth: int) -> None:
            if depth > max_depth:
                return
            try:
                entries = sorted(directory.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
            except OSError:
                return
            for entry in entries:
                try:
                    entry.relative_to(self.root)
                except ValueError:
                    continue
                if entry.name in (".git", "node_modules", ".venv", "__pycache__",
                                  ".pytest_cache", "dist", "data"):
                    continue
                if entry.is_dir():
                    lines.append(f"{'  ' * depth}{entry.name}/")
                    walk(entry, depth + 1)
                else:
                    try:
                        size = entry.stat().st_size
                    except OSError:
                        size = 0
                    lines.append(f"{'  ' * depth}{entry.name} ({size}B)")
        walk(base, 0)
        await self._audit("list_files", "success", path, f"{len(lines)} entries")
        return {"ok": True, "path": str(base.relative_to(self.root)),
                "tree": self._truncate("\n".join(lines))}

    async def create_directory(self, path: str) -> dict[str, Any]:
        target, err = self._resolve_checked(path)
        if err:
            await self._audit("create_directory", "error", path, err)
            return {"ok": False, "error": err}
        try:
            target.mkdir(parents=True, exist_ok=True)
            await self._audit("create_directory", "success", path)
            return {"ok": True, "path": str(target.relative_to(self.root))}
        except (PathEscapeError, OSError) as exc:
            await self._audit("create_directory", "error", path, str(exc))
            return {"ok": False, "error": f"mkdir failed: {exc}"}

    async def delete_file(self, path: str) -> dict[str, Any]:
        """Delete one file inside the sandbox (never directories)."""
        target, err = self._resolve_checked(path)
        if err:
            await self._audit("delete_file", "error", path, err)
            return {"ok": False, "error": err}
        try:
            if not target.is_file():
                raise FileNotFoundError(f"not a file: {path}")
            rel = str(target.relative_to(self.root))
            target.unlink()
            await self._audit("delete_file", "success", path)
            return {"ok": True, "path": rel}
        except (PathEscapeError, FileNotFoundError, OSError) as exc:
            await self._audit("delete_file", "error", path, str(exc))
            return {"ok": False, "error": str(exc)}

    def tool_names(self) -> list[str]:
        return ["read_file", "write_file", "edit_file", "search_files",
                "list_files", "create_directory", "delete_file"]
