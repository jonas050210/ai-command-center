"""Permission system foundation.

Local-first default: the AI gets **no** machine access. Future phases
(Agent Mode, file tools) must request capabilities through this policy
object and every execution is logged via ``tools`` into the
``executions`` table. Nothing here grants implicit access.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Capability(str, Enum):
    FILESYSTEM_READ = "filesystem:read"
    FILESYSTEM_WRITE = "filesystem:write"
    COMMAND_EXECUTE = "command:execute"
    NETWORK_FETCH = "network:fetch"
    GIT_OPERATE = "git:operate"


# Commands future agents may never run (defense-in-depth on top of allowlist)
BLOCKED_COMMANDS = {
    "rm", "rmdir", "del", "erase", "format", "mkfs", "dd", "shutdown",
    "reboot", "reg", "takeown", "icacls", "cipher",
}


@dataclass
class PermissionPolicy:
    """Phase 1-3 policy: deny everything machine-facing by default."""

    granted: set[Capability] = field(default_factory=set)

    def allows(self, capability: Capability) -> bool:
        return capability in self.granted

    def require(self, capability: Capability) -> None:
        from ..core.errors import AppError
        if not self.allows(capability):
            raise AppError(
                f"Capability '{capability.value}' is not granted. "
                "AI access to this machine is restricted by policy.",
                code="PERMISSION_DENIED", status_code=403)

    @staticmethod
    def command_is_blocked(command: str) -> bool:
        first = command.strip().split(maxsplit=1)[0].lower() if command.strip() else ""
        return first in BLOCKED_COMMANDS


DEFAULT_POLICY = PermissionPolicy()  # no capabilities granted in Phase 1-3
