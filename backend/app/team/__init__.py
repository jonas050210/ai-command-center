"""Team Mode (Multi-Model AI Team) — flagship future feature.

Status: NOT IMPLEMENTED. Database foundations (teams, team_members,
tasks, per-member token columns) are already in place so the
orchestrator can integrate cleanly later. The boundary is exposed at
``/api/team`` and returns HTTP 501 — never a fake demo.
"""
from ..core.errors import FeatureNotImplemented


def unavailable() -> None:
    raise FeatureNotImplemented(
        "Team Mode is NOT IMPLEMENTED in this phase. "
        "2–4 models planning, dividing and reviewing work arrives in Phase 5 — "
        "see ROADMAP.")
