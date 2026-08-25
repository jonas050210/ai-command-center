"""Research Mode — boundary module for a later phase.

Status: NOT IMPLEMENTED. The ``research`` table exists as foundation;
the API boundary returns HTTP 501.
"""
from ..core.errors import FeatureNotImplemented


def unavailable() -> None:
    raise FeatureNotImplemented(
        "Research Mode is NOT IMPLEMENTED in this phase. See ROADMAP (Phase 6).")
