"""Projects (P4) — named, sandboxed workspaces.

A project is a directory under ``<workspace_root>/projects/<slug>`` plus a
metadata row. The directory is created on `create`, proven to stay inside
the workspace via the same ``resolve_within`` primitive the tool sandbox
uses, and is NEVER deleted by the API (archiving only — no silent data
loss). Agent runs can target a project; their file tools are then confined
to the project directory instead of the global workspace.
"""
from __future__ import annotations

import re
from pathlib import Path

from ..core.errors import BadRequest, NotFound
from ..db.repo import ProjectsRepo
from ..workspace.paths import resolve_within

MAX_LISTING_ENTRIES = 60
_MAX_COUNT_WALK = 5000


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", name.strip().lower()).strip("-.")
    return slug[:48] or "project"


class ProjectService:
    def __init__(self, repo: ProjectsRepo, workspace_root: Path):
        self.repo = repo
        self.workspace_root = workspace_root.resolve()

    @property
    def _projects_dir(self) -> Path:
        root = self.workspace_root / "projects"
        root.mkdir(parents=True, exist_ok=True)
        return root

    async def create(self, name: str, description: str = "") -> dict:
        name = name.strip()
        if not name:
            raise BadRequest("Project name must not be empty.")
        if len(name) > 80:
            raise BadRequest("Project name too long (max 80 characters).")
        base = slugify(name)
        slug = base
        i = 2
        while (self._projects_dir / slug).exists():
            slug = f"{base}-{i}"
            i += 1
        rel = f"projects/{slug}"
        target = resolve_within(self.workspace_root, rel)
        target.mkdir(parents=True, exist_ok=False)
        row = await self.repo.create(name, description.strip()[:4000], rel)
        return self._decorate(row)

    async def list(self, include_archived: bool = False) -> list[dict]:
        return [self._decorate(r) for r in await self.repo.list(include_archived)]

    async def get(self, pid: int) -> dict:
        row = await self.repo.get(pid)
        if row is None:
            raise NotFound(f"Project {pid} not found.", code="PROJECT_NOT_FOUND")
        return self._decorate(row)

    async def rename(self, pid: int, *, name: str | None, description: str | None) -> dict:
        if name is not None and not name.strip():
            raise BadRequest("Project name must not be empty.")
        row = await self.repo.update(pid, name=name.strip() if name else None,
                                     description=description)
        if row is None:
            raise NotFound(f"Project {pid} not found.", code="PROJECT_NOT_FOUND")
        return self._decorate(row)

    async def set_status(self, pid: int, status: str) -> dict:
        if status not in ("active", "archived"):
            raise BadRequest("Status must be 'active' or 'archived'.")
        row = await self.repo.update(pid, status=status)
        if row is None:
            raise NotFound(f"Project {pid} not found.", code="PROJECT_NOT_FOUND")
        return self._decorate(row)

    # ── paths / agent integration ────────────────────────────────────
    def root_for(self, row: dict) -> Path:
        """Absolute project directory, proven inside the workspace."""
        return resolve_within(self.workspace_root, row["root_path"] or "")

    async def root_for_id(self, pid: int | None) -> tuple[dict | None, Path | None]:
        """(project row, root path) for an agent run; (None, None) = global."""
        if pid is None:
            return None, None
        row = await self.repo.get(pid)
        if row is None:
            raise NotFound(f"Project {pid} not found.", code="PROJECT_NOT_FOUND")
        if row["status"] != "active":
            raise BadRequest(f"Project '{row['name']}' is archived — agent runs are "
                             "disabled for archived projects.", code="PROJECT_ARCHIVED")
        root = self.root_for(row)
        root.mkdir(parents=True, exist_ok=True)
        return row, root

    # ── listing (server-side, read-only) ─────────────────────────────
    def listing(self, row: dict) -> list[str]:
        root = self.root_for(row)
        if not root.is_dir():
            return []
        entries: list[str] = []
        for child in sorted(root.iterdir(), key=lambda p: (p.is_file(), p.name)):
            if child.name.startswith("."):
                continue
            entries.append(child.name + ("/" if child.is_dir() else ""))
            if len(entries) >= MAX_LISTING_ENTRIES:
                entries.append("…")
                break
        return entries

    def _decorate(self, row: dict) -> dict:
        root = self.root_for(row)
        file_count = 0
        exists = root.is_dir()
        if exists:
            try:
                for _ in root.rglob("*"):
                    file_count += 1
                    if file_count >= _MAX_COUNT_WALK:
                        break
            except OSError:
                file_count = -1   # unreadable — honest unknown
        return {**row, "file_count": file_count, "missing": not exists,
                "display_path": row["root_path"] or ""}
