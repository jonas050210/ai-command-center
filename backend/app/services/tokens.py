"""Token estimation helper.

Estimates are *only* used before a response exists (context meter,
pre-flight display) and are always labelled ``estimated``. Exact counts
come from Ollama's ``prompt_eval_count`` / ``eval_count`` fields and are
labelled ``exact``. The two are never conflated.

Heuristic: ~4 characters/token for English text, with a small
per-message structural overhead. Deliberately conservative-only,
clearly labeled.
"""
from __future__ import annotations

import math

CHARS_PER_TOKEN = 4.0
MESSAGE_OVERHEAD = 4  # role/formatting tokens per message


def estimate_tokens(text: str) -> int:
    """Estimated token count for a single text (never labelled exact)."""
    if not text:
        return 0
    return max(1, math.ceil(len(text) / CHARS_PER_TOKEN))


def estimate_messages(messages: list[dict]) -> int:
    return sum(estimate_tokens(m.get("content", "")) + MESSAGE_OVERHEAD
               for m in messages)
