"""Coder Mode (P11) — read-only workspace browser + hardware-honest model profile.

Writes never happen here. Mutations still go through Agent Mode's gateway
(capability → approval → sandbox → audit). This module only:

* lists / reads files inside a project (or the global workspace),
* picks a coding model that actually fits an 8GB card,
* supplies the standing coding skill injected into coder-originated runs.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..core.errors import BadRequest, PathEscapeError
from ..db.repo import ModelsRepo
from ..services.project_service import ProjectService
from ..workspace.paths import resolve_within

MAX_TREE_ENTRIES = 400
MAX_TREE_DEPTH = 6
MAX_READ_BYTES = 80_000
MAX_DIR_CHILDREN = 80

SKIP_DIRS = {
    "node_modules", "__pycache__", "venv", ".venv", ".git", "dist", "build",
    ".next", ".turbo", ".cache", ".pytest_cache", ".ruff_cache", ".mypy_cache",
}

# Ordered preference for *this* product's target hardware (RTX 4060 Ti 8GB).
# Official `qwen3-coder` / `:30b` is ~19GB — listed as too-big, never recommended.
PREFERRED_LOCAL = (
    "qwen2.5-coder:7b",
    "qwen2.5-coder:7b-instruct",
    "qwen3-coder:8b",
    "qwen3-coder:7b",
    "qwen2.5-coder:3b",
    "qwen3:8b",
    "qwen3:4b",
    "llama3.1:8b",
)

PULL_RECOMMEND = "qwen2.5-coder:7b"

TOO_BIG_TOKENS = (
    ":30b", ":32b", ":27b", ":14b", ":70b", ":72b", ":80b", ":480b",
    "qwen3-coder:latest",
)

CODER_SKILLS = """\
You are working in Coder Mode of AI Command Center.

CODING RULES
1. Stay inside this project directory. Never touch secrets (.env, *.key, credentials).
2. Explore first: fs_list then fs_read the relevant files before editing.
3. Prefer fs_edit for surgical changes; fs_write only for new files or full rewrites.
4. After edits, verify with one allow-listed command (pytest, python, ruff, npm test).
5. One command per shell_run call — no chaining. If a command fails, adapt; do not retry identically.
6. Keep changes small and reviewable. Summarize files changed and how you verified.
"""

HARDWARE = {
    "gpu": "RTX 4060 Ti 8GB (design target)",
    "usable_vram_note": "About 7–7.5 GB usable after Windows. 7–8B Q4 at 8k context is the sweet spot.",
    "num_ctx_default": 8192,
    "avoid": "qwen3-coder (30B-A3B, ~19GB) and 14B+ local models — they will not fit.",
}


def _caps(row: dict) -> list[str]:
    try:
        return list(json.loads(row.get("capabilities_json") or "[]"))
    except (TypeError, ValueError):
        return []


def _too_big(name: str) -> bool:
    n = (name or "").lower()
    if n in {"qwen3-coder", "qwen3-coder:latest"}:
        return True
    return any(tok in n for tok in TOO_BIG_TOKENS)


def _is_binary(data: bytes) -> bool:
    if not data:
        return False
    if b"\x00" in data[:512]:
        return True
    # high ratio of non-text bytes in the sample
    sample = data[:512]
    weird = sum(b < 9 or (13 < b < 32) for b in sample)
    return weird > 8


class CoderService:
    def __init__(self, projects: ProjectService, workspace_root: Path,
                 models: ModelsRepo, git=None):
        self.projects = projects
        self.workspace_root = Path(workspace_root).resolve()
        self.models = models
        self.git = git

    async def resolve_scope(self, project_id: int | None) -> tuple[Path, dict | None, str]:
        """Return (absolute root, project row or None, workspace-relative path)."""
        if project_id is None:
            return self.workspace_root, None, "."
        row, root = await self.projects.root_for_id(project_id)
        assert root is not None and row is not None
        rel = row.get("root_path") or "."
        return root, row, rel

    def _safe(self, root: Path, rel: str) -> Path:
        try:
            return resolve_within(root, rel or ".")
        except PathEscapeError:
            raise
        except Exception as exc:
            raise PathEscapeError(
                f"Path '{rel}' escapes the workspace boundary and was blocked.") from exc

    def tree(self, root: Path, rel: str = ".") -> dict[str, Any]:
        target = self._safe(root, rel)
        if not target.exists():
            raise BadRequest(f"Path '{rel}' does not exist.", code="PATH_NOT_FOUND")
        if target.is_file():
            raise BadRequest("tree expects a directory — use /file to read.",
                             code="NOT_A_DIRECTORY")
        truncated = False
        count = 0

        def walk(directory: Path, prefix: str, depth: int) -> list[dict]:
            nonlocal truncated, count
            entries: list[dict] = []
            try:
                children = sorted(directory.iterdir(),
                                  key=lambda p: (p.is_file(), p.name.lower()))
            except OSError:
                return entries
            shown = 0
            for child in children:
                if count >= MAX_TREE_ENTRIES:
                    truncated = True
                    break
                name = child.name
                if name in SKIP_DIRS:
                    continue
                if name.startswith(".") and name != "AGENT.md":
                    continue
                shown += 1
                if shown > MAX_DIR_CHILDREN:
                    truncated = True
                    break
                child_rel = f"{prefix}/{name}" if prefix not in ("", ".") else name
                if child.is_dir():
                    node: dict[str, Any] = {
                        "name": name, "path": child_rel, "kind": "dir",
                    }
                    if depth < MAX_TREE_DEPTH:
                        node["children"] = walk(child, child_rel, depth + 1)
                    else:
                        node["children"] = []
                        truncated = True
                    entries.append(node)
                    count += 1
                elif child.is_file():
                    try:
                        size = child.stat().st_size
                    except OSError:
                        size = None
                    entries.append({
                        "name": name, "path": child_rel, "kind": "file",
                        "size": size,
                    })
                    count += 1
            return entries

        start_rel = "." if rel in ("", ".") else rel.strip().replace("\\", "/").lstrip("/")
        return {
            "path": start_rel,
            "truncated": truncated,
            "entries": walk(target, start_rel if start_rel != "." else "", 0),
            "count": count,
        }

    def read_file(self, root: Path, rel: str) -> dict[str, Any]:
        if not (rel or "").strip() or rel.strip() in (".", "/"):
            raise BadRequest("A file path is required.", code="PATH_REQUIRED")
        target = self._safe(root, rel)
        if not target.exists() or not target.is_file():
            raise BadRequest(f"File not found: {rel}", code="FILE_NOT_FOUND")
        raw = target.read_bytes()
        total = len(raw)
        sample = raw[:MAX_READ_BYTES + 1]
        if _is_binary(sample):
            return {
                "path": rel.replace("\\", "/"),
                "binary": True,
                "size": total,
                "content": None,
                "truncated": False,
                "note": "Binary file — not shown. Use the agent if you need to inspect it.",
            }
        truncated = total > MAX_READ_BYTES
        text = sample[:MAX_READ_BYTES].decode("utf-8", errors="replace")
        return {
            "path": rel.replace("\\", "/"),
            "binary": False,
            "size": total,
            "content": text,
            "truncated": truncated,
            "note": (f"Truncated to first {MAX_READ_BYTES} bytes of {total}."
                     if truncated else None),
        }

    async def profile(self, default_model: str) -> dict[str, Any]:
        rows = await self.models.list(available_only=True)
        catalog: list[dict[str, Any]] = []
        by_name: dict[str, dict] = {}
        for r in rows:
            name = r["name"]
            item = {
                "provider": r["provider"],
                "name": name,
                "is_local": bool(r["is_local"]),
                "is_free": bool(r["is_free"]),
                "available": bool(r["available"]),
                "capabilities": _caps(r),
                "too_big_for_8gb": bool(r["is_local"]) and _too_big(name),
                "parameter_size": r.get("parameter_size"),
            }
            catalog.append(item)
            by_name.setdefault(name, item)

        recommended: list[dict[str, Any]] = []
        for tag in PREFERRED_LOCAL:
            hit = by_name.get(tag)
            if hit and not hit["too_big_for_8gb"]:
                recommended.append({**hit, "reason": "installed · fits 8GB"})
        # any other installed local coder/tools model that isn't too big
        for item in catalog:
            if item in recommended or item["too_big_for_8gb"]:
                continue
            name = item["name"].lower()
            useful = ("coder" in name or "tools" in item["capabilities"]) and item["is_local"]
            if useful:
                recommended.append({**item, "reason": "installed · usable on 8GB"})

        selected = recommended[0] if recommended else None
        if selected is None:
            # last resort: whatever default is, even if it's the smoke model
            fallback = by_name.get(default_model)
            if fallback and not fallback["too_big_for_8gb"]:
                selected = {**fallback, "reason": "app default (pull a coder model for better results)"}
                recommended.append(selected)

        missing = [t for t in PREFERRED_LOCAL[:3] if t not in by_name]
        too_big_installed = [c["name"] for c in catalog if c["too_big_for_8gb"]]

        return {
            "hardware": HARDWARE,
            "selected": selected,
            "recommended": recommended,
            "pull": PULL_RECOMMEND,
            "missing_preferred": missing,
            "too_big_installed": too_big_installed,
            "skills": CODER_SKILLS,
            "note": (
                "Ollama is the runtime. OpenCode is a separate coding CLI — "
                "not used here. OpenAI is paid and CostGuard blocks it while "
                "FREE_ONLY is on."
            ),
        }

    def _flatten_tree(self, entries: list[dict], lines: list[str],
                      limit: int = 80) -> bool:
        truncated = False
        for e in entries:
            if len(lines) >= limit:
                return True
            if e.get("kind") == "dir":
                lines.append(f"{e.get('path', e.get('name'))}/")
                kids = e.get("children") or []
                if self._flatten_tree(kids, lines, limit):
                    return True
            else:
                lines.append(str(e.get("path") or e.get("name")))
        return truncated

    async def context_pack(self, project_id: int | None) -> str:
        """Capped tree + git status for injection into a Coder run."""
        root, project, rel = await self.resolve_scope(project_id)
        listing = self.tree(root, ".")
        lines: list[str] = []
        cut = self._flatten_tree(listing.get("entries") or [], lines, 80)
        if listing.get("truncated") or cut:
            lines.append("…[tree truncated]")
        scope = (f"project “{project['name']}”" if project else "global workspace")
        parts = [
            f"[CODER CONTEXT — {scope}]\n"
            "This listing was injected by the app (not guessed). "
            "Do not spend a step re-listing the whole tree unless you need a subdirectory.\n"
            "Tree:\n" + ("\n".join(lines) if lines else "(empty)")
        ]
        if self.git is not None:
            try:
                st = await self.git.status(".", project_id=project_id) if project_id \
                    else await self.git.status(rel if rel != "." else ".")
                files = st.get("files") or []
                changed = ", ".join(f["path"] for f in files[:20]) or "clean"
                extra = f" (+{len(files) - 20} more)" if len(files) > 20 else ""
                parts.append(
                    f"Git: branch {st.get('branch') or '?'} · "
                    f"{'clean' if st.get('clean') else changed}{extra}")
            except Exception as exc:
                parts.append(f"Git: not a repository ({getattr(exc, 'code', type(exc).__name__)})")
        return "\n".join(parts)
