"""API hardening guards (Phase 4 / P0).

Three independent, pure-ASGI middlewares (no BaseHTTPMiddleware — SSE
streams pass through untouched):

1. ``HostOriginGuardMiddleware`` — DNS-rebinding + cross-site protection:
     * the ``Host`` header must be in the configured allowlist
       (loopback names by default; extras via EXTRA_ALLOWED_HOSTS);
     * a browser ``Origin`` header must match the Host or the dev CORS
       allowlist;
     * ``Sec-Fetch-Site: cross-site`` requests are refused outright.
2. ``ApiTokenMiddleware`` — zero-friction local operation, hardened
   off-loopback exposure:
     * when the server binds a loopback address, every request is allowed
       (TCP already guarantees local-only access);
     * when bound off-loopback, loopback *clients* are still free, but
       every other client must present the API token via
       ``Authorization: Bearer …`` or ``X-API-Key``.
     The token comes from AI_CC_API_TOKEN or is generated once into
   <data_dir>/api.token (chmod 600).
3. ``SecurityHeadersMiddleware`` — nosniff / DENY / no-referrer on
   every response.

The middlewares only ever guard ``/api/*`` — the static SPA is untouched.
"""
from __future__ import annotations

import hmac
import json
import logging
import os
import secrets
import stat
from pathlib import Path
from typing import Awaitable, Callable
from urllib.parse import urlsplit

from ..config import Settings

log = logging.getLogger("aicc.security.guards")

Scope = dict
Receive = Callable[[], Awaitable[dict]]
Send = Callable[[dict], Awaitable[None]]

LOOPBACK_NAMES = {"127.0.0.1", "localhost", "::1", "[::1]"}


def is_loopback_addr(addr: str | None) -> bool:
    if not addr:
        return False
    a = addr.strip().lower().strip("[]")
    return a == "localhost" or a == "::1" or a.startswith("127.")


def split_host(host_header: str) -> str:
    """Hostname portion of a Host header (handles ports + IPv6)."""
    h = host_header.strip().lower()
    if h.startswith("["):  # [::1]:8000
        return h[1:h.find("]")] if "]" in h else h.strip("[]")
    return h.rsplit(":", 1)[0] if ":" in h else h


def host_allowed(host_header: str, allowed: set[str]) -> bool:
    return split_host(host_header) in {a.lower() for a in allowed}


def origin_blocked(origin: str, host_header: str, cors_allowlist: list[str]) -> bool:
    """True when a browser Origin is neither same-host nor allowlisted."""
    try:
        parts = urlsplit(origin)
    except ValueError:
        return True
    origin_host = (parts.netloc or "").lower()
    if not origin_host:
        return True
    if origin_host == host_header.strip().lower():  # same-origin SPA
        return False
    normalized = f"{parts.scheme}://{parts.netloc}".lower()
    return normalized not in {o.rstrip("/").lower() for o in cors_allowlist}


def _json_response(status: int, payload: dict):
    body = json.dumps(payload).encode("utf-8")

    async def respond(receive: Receive, send: Send) -> None:
        await send({"type": "http.response.start", "status": status, "headers": [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
        ]})
        await send({"type": "http.response.body", "body": body})

    return respond


def _error(status: int, code: str, message: str, headers: list[tuple[bytes, bytes]] | None = None):
    body = json.dumps({"error": {"code": code, "message": message, "details": {}}}).encode()
    hdrs = [(b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode())] + (headers or [])

    async def respond(receive: Receive, send: Send) -> None:
        await send({"type": "http.response.start", "status": status, "headers": hdrs})
        await send({"type": "http.response.body", "body": body})

    return respond


class ApiTokenManager:
    """Loads (or lazily creates) the API token used off-loopback."""

    def __init__(self, settings: Settings):
        self._explicit = settings.ai_cc_api_token
        self._path: Path = settings.api_token_path
        self._cached: str | None = None

    @property
    def token(self) -> str:
        if self._explicit:
            return self._explicit
        if self._cached is None:
            if self._path.exists():
                self._cached = self._path.read_text(encoding="utf-8").strip()
            if not self._cached:
                self._cached = secrets.token_urlsafe(32)
                self._path.parent.mkdir(parents=True, exist_ok=True)
                self._path.write_text(self._cached, encoding="utf-8")
                try:
                    os.chmod(self._path, stat.S_IRUSR | stat.S_IWUSR)
                except OSError:  # Windows ACLs differ — best effort
                    pass
                log.info("generated new API token at %s (needed for non-loopback access)",
                         self._path)
        return self._cached

    def matches(self, presented: str | None) -> bool:
        return bool(presented) and hmac.compare_digest(presented.strip(), self.token)


class HostOriginGuardMiddleware:
    def __init__(self, app, settings: Settings):
        self.app = app
        self.allowed_hosts = settings.allowed_hostnames()
        self.cors_allowlist = settings.cors_origin_list()

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http" or not scope.get("path", "").startswith("/api"):
            await self.app(scope, receive, send)
            return
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        host = headers.get("host", "")
        if host and not host_allowed(host, self.allowed_hosts):
            log.warning("blocked request with untrusted Host header: %s", host)
            await _error(403, "HOST_NOT_ALLOWED",
                         "The Host header is not trusted by this server.")(receive, send)
            return
        fetch_site = headers.get("sec-fetch-site", "")
        if fetch_site == "cross-site":
            log.warning("blocked cross-site browser request to %s", scope.get("path"))
            await _error(403, "CROSS_SITE_BLOCKED",
                         "Cross-site browser requests are not allowed.")(receive, send)
            return
        origin = headers.get("origin")
        if origin and origin_blocked(origin, host, self.cors_allowlist):
            log.warning("blocked request with untrusted Origin: %s", origin)
            await _error(403, "ORIGIN_NOT_ALLOWED",
                         "The Origin header is not trusted by this server.")(receive, send)
            return
        await self.app(scope, receive, send)


class ApiTokenMiddleware:
    """Off-loopback token enforcement. Loopback binding ⇒ no token needed."""

    def __init__(self, app, settings: Settings, tokens: ApiTokenManager):
        self.app = app
        self.settings = settings
        self.tokens = tokens

    def _token_required(self, client_host: str | None) -> bool:
        if self.settings.binds_loopback:
            return False                      # TCP already guarantees local-only
        return not is_loopback_addr(client_host)

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if (scope["type"] != "http" or scope.get("method") == "OPTIONS"
                or not scope.get("path", "").startswith("/api")):
            await self.app(scope, receive, send)
            return
        client = scope.get("client")
        client_host = client[0] if client else None
        if not self._token_required(client_host):
            await self.app(scope, receive, send)
            return
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        presented = headers.get("x-api-key")
        auth = headers.get("authorization", "")
        if presented is None and auth.lower().startswith("bearer "):
            presented = auth[7:]
        if self.tokens.matches(presented):
            await self.app(scope, receive, send)
            return
        log.warning("blocked untokenized API request from %s to %s",
                    client_host, scope.get("path"))
        await _error(401, "API_TOKEN_REQUIRED",
                     "This API requires the AI Command Center API token "
                     "(Authorization: Bearer … or X-API-Key) when accessed over the network. "
                     "Find it in <DATA_DIR>/api.token or set AI_CC_API_TOKEN.")(receive, send)


class SecurityHeadersMiddleware:
    HEADERS = [
        (b"x-content-type-options", b"nosniff"),
        (b"x-frame-options", b"deny"),
        (b"referrer-policy", b"no-referrer"),
    ]

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                existing = {k.lower() for k, _ in headers}
                for key, value in self.HEADERS:
                    if key not in existing:
                        headers.append((key, value))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_headers)
