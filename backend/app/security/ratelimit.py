"""Sliding-window rate limiting on sensitive API endpoints.

Local-first design: limits are generous for a single interactive user but
stop runaway scripts and hostile local webpages from hammering expensive
operations (model pulls, chat streams, settings changes). Pure ASGI — SSE
responses stream through untouched.
"""
from __future__ import annotations

import re
import time
import json
import logging
from collections import defaultdict, deque
from typing import Awaitable, Callable

from ..config import Settings

log = logging.getLogger("aicc.security.ratelimit")

Scope = dict
Receive = Callable[[], Awaitable[dict]]
Send = Callable[[dict], Awaitable[None]]


class RateLimitRule:
    def __init__(self, methods: tuple[str, ...] | None, pattern: str, per_minute: int):
        self.methods = methods
        self.pattern = re.compile(pattern)
        self.per_minute = per_minute

    def matches(self, method: str, path: str) -> bool:
        if self.methods is not None and method not in self.methods:
            return False
        return bool(self.pattern.match(path))


# Most-specific rules first; the catch-all is last.
DEFAULT_RULES = [
    RateLimitRule(("POST",), r"^/api/models/pull$", 6),
    RateLimitRule(("POST",), r"^/api/models/refresh$", 10),
    RateLimitRule(("POST",), r"^/api/models/test$", 12),
    RateLimitRule(("DELETE",), r"^/api/models/", 20),
    RateLimitRule(("POST",), r"^/api/providers/", 20),
    RateLimitRule(("POST",), r"^/api/chat/", 40),
    RateLimitRule(("POST",), r"^/api/agent/", 30),
    RateLimitRule(("POST",), r"^/api/team/", 10),
    RateLimitRule(("POST",), r"^/api/research/", 10),
    RateLimitRule(("PUT",), r"^/api/settings$", 40),
    RateLimitRule(("POST",), r"^/api/compare/", 20),
    RateLimitRule(None, r"^/api/", 480),
]


class SlidingWindowLimiter:
    def __init__(self):
        self._hits: dict[tuple[str, str], deque] = defaultdict(deque)

    def check(self, key: tuple[str, str], limit: int, window: float = 60.0) -> tuple[bool, float]:
        """Returns (allowed, retry_after_seconds)."""
        now = time.monotonic()
        q = self._hits[key]
        while q and now - q[0] > window:
            q.popleft()
        if len(q) >= limit:
            return False, max(1.0, window - (now - q[0]))
        q.append(now)
        return True, 0.0


def _too_many(retry_after: float):
    body = json.dumps({"error": {
        "code": "RATE_LIMITED",
        "message": "Too many requests — slow down and retry.",
        "details": {"retry_after_s": round(retry_after, 1)}}}).encode()

    async def respond(receive: Receive, send: Send) -> None:
        await send({"type": "http.response.start", "status": 429, "headers": [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
            (b"retry-after", str(max(1, int(retry_after))).encode()),
        ]})
        await send({"type": "http.response.body", "body": body})

    return respond


class RateLimitMiddleware:
    def __init__(self, app, settings: Settings, limiter: SlidingWindowLimiter | None = None):
        self.app = app
        self.enabled = settings.enable_rate_limits
        self.limiter = limiter or SlidingWindowLimiter()
        self.rules = DEFAULT_RULES

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if (not self.enabled or scope["type"] != "http" or scope.get("method") == "OPTIONS"
                or not scope.get("path", "").startswith("/api")):
            await self.app(scope, receive, send)
            return
        method = scope.get("method", "GET")
        path = scope.get("path", "")
        for rule in self.rules:
            if rule.matches(method, path):
                client = scope.get("client")
                client_key = (client[0] if client else "unknown")
                allowed, retry = self.limiter.check((client_key, rule.pattern.pattern),
                                                    rule.per_minute)
                if not allowed:
                    log.warning("rate-limited %s %s from %s (retry in %.0fs)",
                                method, path, client_key, retry)
                    await _too_many(retry)(receive, send)
                    return
                break
        await self.app(scope, receive, send)
