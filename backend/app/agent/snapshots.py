"""Per-run file snapshots — undo the mutations an agent actually made.

Only files touched by ``fs_write`` / ``fs_edit`` after approval are
recorded. Undo restores originals and deletes files the run created.
Archive/undo never walks outside the run's sandbox root.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from ..workspace.paths import resolve_within

log = logging.getLogger("aicc.snapshots")

MAX_FILE_BYTES = 2_000_000
MAX_FILES = 80


class RunSnapshot:
    def __init__(self, store_root: Path, run_id: str):
        self.run_id = run_id
        self.dir = Path(store_root) / run_id
        self.manifest_path = self.dir / "manifest.json"
        self._entries: list[dict] = []
        self._seen: set[str] = set()
        self.truncated = False
        if self.manifest_path.is_file():
            try:
                data = json.loads(self.manifest_path.read_text(encoding="utf-8"))
                self._entries = list(data.get("files") or [])
                self._seen = {e["path"] for e in self._entries if e.get("path")}
                self.truncated = bool(data.get("truncated"))
            except (OSError, ValueError):
                self._entries = []

    def record(self, rel: str, sandbox_root: Path) -> None:
        rel = (rel or "").replace("\\", "/").lstrip("/")
        if not rel or rel in self._seen:
            return
        if len(self._entries) >= MAX_FILES:
            self.truncated = True
            return
        try:
            target = resolve_within(sandbox_root, rel)
        except Exception:
            return
        self._seen.add(rel)
        existed = target.is_file()
        store_name = None
        if existed:
            try:
                raw = target.read_bytes()
            except OSError:
                return
            if len(raw) > MAX_FILE_BYTES:
                self.truncated = True
                self._entries.append({"path": rel, "existed": True, "store": None,
                                      "skipped": "too_large"})
                self._persist()
                return
            self.dir.mkdir(parents=True, exist_ok=True)
            store_name = f"{len(self._entries)}.bin"
            (self.dir / store_name).write_bytes(raw)
        self._entries.append({"path": rel, "existed": existed, "store": store_name})
        self._persist()

    def _persist(self) -> None:
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            self.manifest_path.write_text(json.dumps({
                "run_id": self.run_id,
                "truncated": self.truncated,
                "files": self._entries,
            }, indent=2), encoding="utf-8")
        except OSError:
            log.warning("could not persist snapshot for run %s", self.run_id)

    def info(self) -> dict:
        return {
            "run_id": self.run_id,
            "exists": bool(self._entries),
            "truncated": self.truncated,
            "files": [{"path": e["path"], "existed": e.get("existed"),
                       "skipped": e.get("skipped")} for e in self._entries],
            "count": len(self._entries),
        }

    def restore(self, sandbox_root: Path) -> dict:
        if not self._entries:
            return {"restored": 0, "deleted": 0, "skipped": 0, "files": []}
        restored = deleted = skipped = 0
        done: list[str] = []
        for entry in reversed(self._entries):
            rel = entry.get("path") or ""
            try:
                target = resolve_within(sandbox_root, rel)
            except Exception:
                skipped += 1
                continue
            if not entry.get("existed"):
                if target.is_file():
                    try:
                        target.unlink()
                        deleted += 1
                        done.append(rel)
                    except OSError:
                        skipped += 1
                continue
            store = entry.get("store")
            if not store:
                skipped += 1
                continue
            src = self.dir / store
            if not src.is_file():
                skipped += 1
                continue
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(src.read_bytes())
                restored += 1
                done.append(rel)
            except OSError:
                skipped += 1
        return {"restored": restored, "deleted": deleted, "skipped": skipped,
                "files": done, "truncated": self.truncated}


def snapshot_for(store_root: Path, run_id: str) -> RunSnapshot:
    return RunSnapshot(store_root, run_id)
