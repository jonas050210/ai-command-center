"""Memory + skills service (P8).

Reads are cheap and sync-free: AGENT.md files are plain workspace reads
(proven inside the sandbox via resolve_within), memories are one SQL
query. Writes from the model side only ever happen through the memory_*
tools (gateway + approval + audit). Size caps are hard and truncation is
honest — the run's context shows exactly what the model saw.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from ..db.repo import MemoriesRepo
from ..workspace.paths import resolve_within

log = logging.getLogger("aicc.memory")

AGENT_MD_NAME = "AGENT.md"
AGENT_MD_MAX = 6000
MEMORIES_MAX_ROWS = 20
MEMORIES_MAX_CHARS = 3000
KEY_MAX = 60
CONTENT_MAX = 2000

_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,59}$")


class MemoryService:
    def __init__(self, repo: MemoriesRepo):
        self.repo = repo

    # ── CRUD (shared by the REST surface and the memory_* tools) ─────
    async def save(self, key: str, content: str, source: str = "user") -> dict:
        key = (key or "").strip()[:KEY_MAX]
        if not _KEY_RE.match(key):
            raise ValueError(
                "Memory key must be 1–60 chars: letters, digits, space, "
                "'.', '_', '-'.")
        content = (content or "").strip()
        if not content:
            raise ValueError("Memory content must not be empty.")
        await self.repo.upsert(key, content[:CONTENT_MAX], source)
        return {"key": key, "content": content[:CONTENT_MAX], "source": source}

    async def forget(self, key: str) -> bool:
        return await self.repo.delete_by_key((key or "").strip())

    async def delete(self, mem_id: int) -> bool:
        return await self.repo.delete(mem_id)

    async def list(self, limit: int = 100) -> list[dict]:
        return await self.repo.list(limit)

    async def search(self, query: str, limit: int = 10) -> list[dict]:
        query = (query or "").strip()
        if not query:
            return await self.repo.list(limit)
        return await self.repo.search(query, limit)

    # ── AGENT.md (workspace-level standing instructions) ─────────────
    @staticmethod
    def read_agent_md(root: Path) -> str | None:
        try:
            target = resolve_within(root, AGENT_MD_NAME)
        except Exception:
            return None
        if not target.is_file():
            return None
        try:
            return target.read_text(encoding="utf-8", errors="replace")[
                :AGENT_MD_MAX].strip() or None
        except OSError:
            return None

    # ── prompt assembly (what the next run will actually see) ────────
    async def build_skills_text(self, workspace_root: Path,
                                project_root: Path | None = None) -> str | None:
        """AGENT.md chain: workspace first, project appended."""
        parts = []
        ws_md = self.read_agent_md(workspace_root)
        if ws_md:
            parts.append(f"[AGENT.md — workspace]\n{ws_md}")
        if project_root is not None and project_root != workspace_root:
            pr_md = self.read_agent_md(project_root)
            if pr_md:
                parts.append(f"[AGENT.md — project]\n{pr_md}")
        return "\n\n".join(parts) if parts else None

    async def memory_text(self) -> str | None:
        rows = await self.repo.list(MEMORIES_MAX_ROWS)
        if not rows:
            return None
        lines = []
        total = 0
        for r in rows:
            line = f"- {r['key']}: {r['content']}"
            if total + len(line) > MEMORIES_MAX_CHARS:
                lines.append("- …[memory truncated: cap reached]")
                break
            lines.append(line)
            total += len(line)
        return "\n".join(lines)

    async def context_summary(self, workspace_root: Path) -> dict[str, Any]:
        rows = await self.repo.list(MEMORIES_MAX_ROWS + 1)
        return {"memory_count": await self.repo.count(),
                "memories_preview": rows[:5],
                "agent_md": self.read_agent_md(workspace_root) is not None}
