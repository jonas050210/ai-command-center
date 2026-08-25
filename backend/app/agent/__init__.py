"""Agent Mode — controlled autonomous agent (PLAN → EXECUTE → VERIFY → FIX → FINALIZE).

The agent acts only through the sandboxed file tools and allowlisted
command runner; every action is audited and every model call is metered
and cost-guarded. See ``engine.AgentEngine``.
"""
from .engine import AgentEngine

__all__ = ["AgentEngine"]
