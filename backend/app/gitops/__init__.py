"""Git/GitHub integration — boundary module for a later phase.

Status: NOT IMPLEMENTED. Git operations will execute through
security.permissions (Capability.GIT_OPERATE) and tools.audit logging.
"""
from ..core.errors import FeatureNotImplemented


def unavailable() -> None:
    raise FeatureNotImplemented(
        "Git/GitHub integration is NOT IMPLEMENTED in this phase. See ROADMAP (Phase 7).")
