"""Research Mode — real multi-source research with citations.

Only real sources are stored; searches with no results fail honestly.
See ``engine.ResearchEngine``.
"""
from .engine import ResearchEngine

__all__ = ["ResearchEngine"]
