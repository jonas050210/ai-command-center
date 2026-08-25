"""Path containment — the core of the future filesystem sandbox."""
from __future__ import annotations

from pathlib import Path

from ..core.errors import PathEscapeError


def resolve_within(root: Path, candidate: str | Path) -> Path:
    """Resolve *candidate* against *root* and prove it stays inside.

    Blocks absolute escapes and ``..`` traversal on every platform —
    including Windows-style ``..\\`` separators and drive/UNC absolute
    paths, which POSIX would otherwise treat as innocent filenames.
    Symlinks are resolved via ``Path.resolve``. Raises PathEscapeError
    otherwise.
    """
    root_resolved = Path(root).resolve()
    raw = str(candidate).replace("\\", "/")   # unify separators first
    target = Path(raw)
    if not target.is_absolute():
        target = root_resolved / target
    resolved = target.resolve()
    if resolved != root_resolved and root_resolved not in resolved.parents:
        raise PathEscapeError(
            f"Path '{candidate}' escapes the workspace boundary and was blocked.")
    return resolved
