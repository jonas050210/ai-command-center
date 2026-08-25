"""Team Mode — Multi-Model AI Team (flagship).

2–4 models: task intake → joint analysis → master plan → role assignment
→ execution → review → fix → final review → delivery. Shared state on the
team board; per-model tokens and TEAM TOTAL; always €0.00 (CostGuard).
"""
from .engine import TeamEngine

__all__ = ["TeamEngine"]
