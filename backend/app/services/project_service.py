"""Projects (P4/P12) — named sandboxed workspaces + linked folders.

Created projects live under ``<workspace_root>/projects/<slug>``.
Attached (linked) projects point at an existing folder the user owns;
tools are still path-contained to *that* root. Archiving never deletes
linked files.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from ..core.errors import BadRequest, NotFound
from ..db.repo import ProjectsRepo
from ..workspace.paths import resolve_within

MAX_LISTING_ENTRIES = 60
_MAX_COUNT_WALK = 5000

_POSIX_FORBIDDEN = (
    "/etc", "/usr", "/bin", "/sbin", "/boot", "/proc", "/sys", "/dev",
    "/root", "/var/run", "/var/lib", "/Library", "/System",
)
_WIN_FORBIDDEN_PARTS = {
    "windows", "system32", "program files", "program files (x86)",
    "programdata", "system volume information", "$recycle.bin",
}


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", name.strip().lower()).strip("-.")
    return slug[:48] or "project"


def validate_attach_path(raw: str, data_dir: Path | None,
                         workspace_root: Path | None = None) -> Path:
    """Prove ``raw`` is a real, attachable directory. Never a system root."""
    text = (raw or "").strip()
    if not text:
        raise BadRequest("Attach path must not be empty.", code="PATH_REQUIRED")
    unified = text.replace("\\", "/")
    if unified.startswith("//"):
        raise BadRequest("UNC paths cannot be attached.", code="PATH_FORBIDDEN")
    candidate = Path(text).expanduser()
    if not candidate.is_absolute():
        raise BadRequest("Attach path must be absolute "
                         "(e.g. D:\\\\code\\\\my-app or /home/you/src/app).",
                         code="PATH_NOT_ABSOLUTE")
    try:
        resolved = candidate.resolve()
    except OSError as exc:
        raise BadRequest(f"Path could not be resolved: {exc}",
                         code="PATH_NOT_FOUND") from exc
    if not resolved.is_dir():
        raise BadRequest("Path does not exist or is not a directory.",
                         code="PATH_NOT_FOUND")
    if resolved.parent == resolved:
        raise BadRequest("Cannot attach a filesystem root.", code="PATH_FORBIDDEN")
    as_posix = resolved.as_posix()
    for prefix in _POSIX_FORBIDDEN:
        if as_posix == prefix or as_posix.startswith(prefix + "/"):
            raise BadRequest(f"Cannot attach system directory '{prefix}'.",
                             code="PATH_FORBIDDEN")
    for part in resolved.parts:
        if part.lower() in _WIN_FORBIDDEN_PARTS:
            raise BadRequest("Cannot attach a Windows system directory.",
                             code="PATH_FORBIDDEN")
    if data_dir is not None:
        try:
            data = Path(data_dir).resolve()
        except OSError:
            data = None
        if data is not None:
            if resolved == data or data in resolved.parents or resolved in data.parents:
                raise BadRequest(
                    "Cannot attach the app data directory or any of its "
                    "ancestors/children (that path holds the credential vault).",
                    code="PATH_FORBIDDEN")
    return resolved


class ProjectService:
    def __init__(self, repo: ProjectsRepo, workspace_root: Path,
                 data_dir: Path | None = None):
        self.repo = repo
        self.workspace_root = workspace_root.resolve()
        self.data_dir = Path(data_dir).resolve() if data_dir else None

    @property
    def _projects_dir(self) -> Path:
        root = self.workspace_root / "projects"
        root.mkdir(parents=True, exist_ok=True)
        return root

    def is_linked(self, row: dict) -> bool:
        return bool(int(row.get("linked") or 0))

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
        row = await self.repo.create(name, description.strip()[:4000], rel, linked=False)
        return self._decorate(row)

    async def attach(self, path: str, name: str = "", description: str = "") -> dict:
        resolved = validate_attach_path(path, self.data_dir, self.workspace_root)
        for existing in await self.repo.list(include_archived=True):
            if not self.is_linked(existing):
                continue
            try:
                if Path(existing["root_path"]).resolve() == resolved:
                    raise BadRequest(
                        f"That folder is already attached as '{existing['name']}'.",
                        code="PROJECT_ALREADY_LINKED")
            except OSError:
                continue
        label = (name or "").strip() or resolved.name or "linked-project"
        if len(label) > 80:
            label = label[:80]
        row = await self.repo.create(label, description.strip()[:4000],
                                     str(resolved), linked=True)
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
        """Absolute project directory.

        Linked projects use the stored absolute path (tools stay inside
        *that* root). Created projects stay inside the workspace.
        """
        if self.is_linked(row):
            return Path(row["root_path"] or "").expanduser().resolve()
        return resolve_within(self.workspace_root, row["root_path"] or "")

    async def root_for_id(self, pid: int | None, *,
                          allow_archived: bool = False) -> tuple[dict | None, Path | None]:
        """(project row, root path) for an agent run; (None, None) = global."""
        if pid is None:
            return None, None
        row = await self.repo.get(pid)
        if row is None:
            raise NotFound(f"Project {pid} not found.", code="PROJECT_NOT_FOUND")
        if row["status"] != "active" and not allow_archived:
            raise BadRequest(f"Project '{row['name']}' is archived — agent runs are "
                             "disabled for archived projects.", code="PROJECT_ARCHIVED")
        root = self.root_for(row)
        if self.is_linked(row):
            if not root.is_dir():
                raise BadRequest(
                    f"Linked folder for '{row['name']}' is missing: {root}",
                    code="PROJECT_MISSING")
            return row, root
        root.mkdir(parents=True, exist_ok=True)
        return row, root

    # ── listing (server-side, read-only) ─────────────────────────────
    def listing(self, row: dict) -> list[str]:
        root = self.root_for(row)
        if not root.is_dir():
            return []
        entries: list[str] = []
        try:
            children = sorted(root.iterdir(), key=lambda p: (p.is_file(), p.name))
        except OSError:
            return []
        for child in children:
            if child.name.startswith("."):
                continue
            entries.append(child.name + ("/" if child.is_dir() else ""))
            if len(entries) >= MAX_LISTING_ENTRIES:
                entries.append("…")
                break
        return entries

    def _decorate(self, row: dict) -> dict:
        linked = self.is_linked(row)
        try:
            root = self.root_for(row)
            exists = root.is_dir()
        except Exception:
            root = None
            exists = False
        file_count = 0
        if exists and root is not None:
            try:
                for _ in root.rglob("*"):
                    file_count += 1
                    if file_count >= _MAX_COUNT_WALK:
                        break
            except OSError:
                file_count = -1
        display = str(root) if linked and root is not None else (row["root_path"] or "")
        out = {**row, "linked": linked, "file_count": file_count,
               "missing": not exists, "display_path": display}
        return out
