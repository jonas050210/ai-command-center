"""Workspace boundary — future filesystem sandbox root.

Phase 4+ agents and tools may only ever operate inside the configured
workspace root; ``resolve_within`` enforces containment.
"""
from .paths import resolve_within

__all__ = ["resolve_within"]
