"""Path containment — the core of the filesystem sandbox."""
from __future__ import annotations

import re
from pathlib import Path

from ..core.errors import PathEscapeError

# Windows absolute forms that POSIX would wrongly treat as innocent
# filenames — platform-independent defense.
_DRIVE_ABS_RE = re.compile(r"^[a-zA-Z]:(/|$)")


def resolve_within(root: Path, candidate: str | Path) -> Path:
    """Resolve *candidate* against *root* and prove it stays inside.

    Blocks absolute escapes and ``..`` traversal on every platform —
    including Windows-style ``..\\`` separators, drive-letter paths and
    UNC shares, which POSIX would otherwise treat as innocent filenames.
    Symlinks are resolved via ``Path.resolve``. Raises PathEscapeError
    otherwise.
    """
    root_resolved = Path(root).resolve()
    raw = str(candidate).replace("\\", "/")   # unify separators first
    if raw.startswith("//") or _DRIVE_ABS_RE.match(raw):
        raise PathEscapeError(
            f"Path '{candidate}' escapes the workspace boundary and was blocked.")
    target = Path(raw)
    if not target.is_absolute():
        target = root_resolved / target
    resolved = target.resolve()
    if resolved != root_resolved and root_resolved not in resolved.parents:
        raise PathEscapeError(
            f"Path '{candidate}' escapes the workspace boundary and was blocked.")
    return resolved
