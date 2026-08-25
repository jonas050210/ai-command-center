"""Tool execution foundation.

No machine tools are exposed to models in Phase 1-3 (Agent Mode is a
later phase). What exists *now* is the audit trail: every future tool
execution will be logged to the ``executions`` table via this module.
"""
from .audit import log_execution

__all__ = ["log_execution"]
