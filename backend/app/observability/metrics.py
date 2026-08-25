"""Minimal in-process metrics counters (observability foundation).

Lifetime totals are persisted in SQLite (usage_events); these counters
cover the *current process session* since boot.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class Metrics:
    started_at: float = field(default_factory=time.time)
    http_requests: int = 0
    chat_requests: int = 0
    chat_errors: int = 0
    blocked_paid_requests: int = 0
    session_input_tokens: int = 0
    session_output_tokens: int = 0
    session_cost_eur: float = 0.0

    def uptime_s(self) -> float:
        return round(time.time() - self.started_at, 1)


metrics = Metrics()
