"""Tool execution audit logging."""
from __future__ import annotations

from ..db.repo import ExecutionsRepo


async def log_execution(repo: ExecutionsRepo, *, kind: str, status: str,
                        command: str | None = None, actor: str = "user",
                        exit_code: int | None = None, log_text: str = "") -> int:
    return await repo.log(kind=kind, status=status, command=command, actor=actor,
                          exit_code=exit_code, log_text=log_text)
