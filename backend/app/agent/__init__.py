"""Agent Mode — boundary module for a later phase.

Status: NOT IMPLEMENTED. The API router exposes this boundary at
``/api/agent`` and returns HTTP 501 so nothing ever pretends to work.
When implemented, the agent engine will live here and will execute
ONLY through security.permissions (capability checks) + tools.audit
(execution logging) inside workspace/ boundaries.
"""
from ..core.errors import FeatureNotImplemented


def unavailable() -> None:
    raise FeatureNotImplemented(
        "Agent Mode is NOT IMPLEMENTED in this phase. "
        "It is a planned feature — see ROADMAP (Phase 4).")
