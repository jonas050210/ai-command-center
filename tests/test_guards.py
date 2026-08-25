"""P0 hardening tests — token guard, host/origin guard, rate limiting,
security headers, log rotation/redaction, context clamping and the
model-router provider resolution fixes."""
from __future__ import annotations

import logging

import httpx
import pytest

from backend.app.config import DEFAULT_MODEL_NAME, Settings
from backend.app.db.migrations import migrate
from backend.app.main import create_app
from backend.app.observability.logging import redact, setup_logging
from backend.app.security.guards import (ApiTokenManager, host_allowed,
                                         is_loopback_addr, origin_blocked,
                                         split_host)


def make_settings(tmp_path, **kw) -> Settings:
    base = dict(data_dir=tmp_path / "data", default_model=DEFAULT_MODEL_NAME,
                free_only=True, max_spend=0.0, ollama_host="http://testserver")
    base.update(kw)
    return Settings(**base)


class FixedClientApp:
    """ASGI wrapper pinning the client address (simulates remote callers)."""

    def __init__(self, app, host: str):
        self.app, self.host = app, host

    async def __call__(self, scope, receive, send):
        scope["client"] = (self.host, 4321)
        await self.app(scope, receive, send)


async def boot(settings: Settings, client_host: str = "127.0.0.1"):
    app = create_app(settings)
    svc = app.state.services
    await svc.db.connect()
    await migrate(svc.db)
    wrapped = FixedClientApp(app, client_host)
    transport = httpx.ASGITransport(app=wrapped)
    client = httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1")
    return app, svc, client


# ── helpers (unit) ───────────────────────────────────────────────────
class TestHelpers:
    def test_split_host(self):
        assert split_host("127.0.0.1:8000") == "127.0.0.1"
        assert split_host("localhost") == "localhost"
        assert split_host("[::1]:8000") == "::1"
        assert split_host("EXAMPLE.com:443") == "example.com"

    def test_is_loopback(self):
        assert is_loopback_addr("127.0.0.1")
        assert is_loopback_addr("127.44.1.9")
        assert is_loopback_addr("::1")
        assert is_loopback_addr("localhost")
        assert not is_loopback_addr("10.0.0.5")
        assert not is_loopback_addr(None)

    def test_host_allowed(self):
        allowed = {"127.0.0.1", "localhost", "::1"}
        assert host_allowed("127.0.0.1:8000", allowed)
        assert host_allowed("localhost", allowed)
        assert not host_allowed("evil.example.com", allowed)

    def test_origin_blocked(self):
        # same-host origin is fine (SPA served by the app)
        assert not origin_blocked("http://127.0.0.1:8000", "127.0.0.1:8000", [])
        # dev allowlist origin is fine
        assert not origin_blocked("http://localhost:5173", "127.0.0.1:8000",
                                  ["http://localhost:5173"])
        # anything else is blocked
        assert origin_blocked("https://evil.com", "127.0.0.1:8000",
                              ["http://localhost:5173"])
        assert origin_blocked("not-a-url", "127.0.0.1:8000", [])


# ── API token guard ──────────────────────────────────────────────────
class TestApiTokenGuard:
    async def test_loopback_binding_needs_no_token(self, tmp_path):
        """Default local-first mode: loopback binding → zero friction."""
        app, svc, client = await boot(make_settings(tmp_path), "127.0.0.1")
        try:
            assert svc.settings.binds_loopback
            r = await client.get("/api/health")
            assert r.status_code == 200
        finally:
            await client.aclose(); await svc.db.close()

    async def test_offloopback_loopback_client_still_free(self, tmp_path):
        """Bound to 0.0.0.0: local processes still need no token."""
        app, svc, client = await boot(make_settings(tmp_path, host="0.0.0.0"),
                                      client_host="127.0.0.1")
        try:
            r = await client.get("/api/health")
            assert r.status_code == 200
        finally:
            await client.aclose(); await svc.db.close()

    async def test_offloopback_remote_client_requires_token(self, tmp_path):
        token = "unit-test-token-0123456789"
        app, svc, client = await boot(
            make_settings(tmp_path, host="0.0.0.0", ai_cc_api_token=token),
            client_host="10.0.0.44")
        try:
            denied = await client.get("/api/health")
            assert denied.status_code == 401
            assert denied.json()["error"]["code"] == "API_TOKEN_REQUIRED"

            wrong = await client.get("/api/health", headers={"X-API-Key": "nope"})
            assert wrong.status_code == 401

            ok = await client.get("/api/health",
                                  headers={"Authorization": f"Bearer {token}"})
            assert ok.status_code == 200
        finally:
            await client.aclose(); await svc.db.close()

    async def test_token_file_generated_with_perms(self, tmp_path):
        s = make_settings(tmp_path)
        tm = ApiTokenManager(s)
        token = tm.token
        assert tm.token == token          # stable across reads
        assert s.api_token_path.exists()
        assert ApiTokenManager(s).token == token  # loads from file
        mode = s.api_token_path.stat().st_mode & 0o777
        assert mode & 0o077 == 0          # no group/other access

    async def test_static_spa_not_token_guarded(self, tmp_path):
        app, svc, client = await boot(
            make_settings(tmp_path, host="0.0.0.0",
                          ai_cc_api_token="tok"), client_host="10.0.0.44")
        try:
            r = await client.get("/")     # SPA shell must stay reachable
            assert r.status_code == 200
        finally:
            await client.aclose(); await svc.db.close()


# ── host / origin guard ──────────────────────────────────────────────
class TestHostOriginGuard:
    async def test_untrusted_host_blocked(self, tmp_path):
        app, svc, client = await boot(make_settings(tmp_path))
        try:
            r = await client.get("/api/health", headers={"Host": "evil.example.com"})
            assert r.status_code == 403
            assert r.json()["error"]["code"] == "HOST_NOT_ALLOWED"
        finally:
            await client.aclose(); await svc.db.close()

    async def test_cross_site_origin_blocked_same_origin_ok(self, tmp_path):
        app, svc, client = await boot(make_settings(tmp_path))
        try:
            bad = await client.get("/api/health", headers={"Origin": "https://evil.com"})
            assert bad.status_code == 403
            assert bad.json()["error"]["code"] == "ORIGIN_NOT_ALLOWED"

            good = await client.get("/api/health",
                                    headers={"Origin": "http://127.0.0.1"})
            assert good.status_code == 200

            fetch_site = await client.post(
                "/api/chat/completions", json={"content": "hi"},
                headers={"Sec-Fetch-Site": "cross-site"})
            assert fetch_site.status_code == 403
            assert fetch_site.json()["error"]["code"] == "CROSS_SITE_BLOCKED"
        finally:
            await client.aclose(); await svc.db.close()

    async def test_extra_allowed_hosts(self, tmp_path):
        s = make_settings(tmp_path, host="0.0.0.0",
                          extra_allowed_hosts="my-pc.lan, workstation")
        assert "my-pc.lan" in s.allowed_hostnames()
        assert "workstation" in s.allowed_hostnames()


# ── security headers ─────────────────────────────────────────────────
class TestSecurityHeaders:
    async def test_headers_present(self, tmp_path):
        app, svc, client = await boot(make_settings(tmp_path))
        try:
            r = await client.get("/api/health")
            assert r.headers["x-content-type-options"] == "nosniff"
            assert r.headers["x-frame-options"] == "deny"
            assert r.headers["referrer-policy"] == "no-referrer"
        finally:
            await client.aclose(); await svc.db.close()


# ── rate limiting ────────────────────────────────────────────────────
class TestRateLimiting:
    async def test_burst_is_limited(self, tmp_path):
        app, svc, client = await boot(make_settings(tmp_path))
        try:
            codes = [await client.post("/api/models/refresh") for _ in range(11)]
            assert [r.status_code for r in codes[:10]] == [200] * 10
            assert codes[10].status_code == 429
            assert codes[10].json()["error"]["code"] == "RATE_LIMITED"
            assert "retry-after" in codes[10].headers
        finally:
            await client.aclose(); await svc.db.close()

    async def test_disabled_limiter_allows_bursts(self, tmp_path):
        app, svc, client = await boot(
            make_settings(tmp_path, enable_rate_limits=False))
        try:
            codes = [await client.post("/api/models/refresh") for _ in range(15)]
            assert all(r.status_code == 200 for r in codes)
        finally:
            await client.aclose(); await svc.db.close()


# ── logging hardening ────────────────────────────────────────────────
class TestLogging:
    def test_secret_redaction(self):
        assert "sk-or-abcdef1234567890" not in redact("key sk-or-abcdef1234567890 ok")
        assert "***" in redact("Authorization: Bearer abcdef123456")
        assert "abcdef123456" not in redact("Bearer abcdef123456")

    def test_log_rotation(self, tmp_path):
        log_dir = tmp_path / "logs"
        setup_logging("INFO", log_dir, max_bytes=100_000, backups=1)
        logger = logging.getLogger("aicc.rotation.test")
        for i in range(800):
            logger.info("rotation filler line %d %s", i, "x" * 120)
        for h in logging.getLogger().handlers:
            h.flush()
        main = log_dir / "app.log"
        assert main.exists()
        assert main.stat().st_size <= 120_000       # rotated, not unbounded
        assert (log_dir / "app.log.1").exists()


# ── context clamping (audit bug §3.3-2) ──────────────────────────────
class TestContextClamp:
    async def test_num_ctx_clamped_to_model_context(self, api):
        await api.client.post("/api/models/refresh")
        # shrink the catalog row's context length
        await api.svc.db.execute("UPDATE models SET context_length=2048")
        r = await api.client.post("/api/chat/completions",
                                  json={"content": "hello clamp test"})
        assert r.status_code == 200
        assert api.ollama.last_options is not None
        assert api.ollama.last_options.num_ctx == 2048   # min(settings 8192, model 2048)


# ── router provider resolution (audit bug §3.3-5) ────────────────────
class TestProviderResolution:
    async def test_default_provider_from_settings(self, services_env):
        env = services_env
        provider, model, row = await env.router.resolve(None, None)
        assert provider.name == "ollama"
        assert model == env.settings.default_model

    async def test_find_provider_for_prefers_local(self, services_env):
        env = services_env
        row = {"provider": "cloudy", "name": "dup-model", "display_name": "dup",
               "is_local": False, "is_free": False, "context_length": None,
               "size_bytes": None, "parameter_size": None, "quantization": None,
               "family": None, "families": [], "capabilities": [], "categories": [],
               "available": True, "status": "available", "raw": {}}
        await env.models.upsert_from_provider(row)
        assert await env.models.find_provider_for("dup-model") == "cloudy"
        row2 = {**row, "provider": "ollama", "is_local": True, "is_free": True}
        await env.models.upsert_from_provider(row2)
        assert await env.models.find_provider_for("dup-model") == "ollama"

    async def test_unknown_provider_error_lists_providers(self, services_env):
        with pytest.raises(Exception) as exc:
            await services_env.router.resolve("nope", None)
        assert "ollama" in str(exc.value)


# ── model delete semantics (audit bug §3.3-4) ────────────────────────
class TestModelDelete:
    async def test_delete_removes_from_runtime_and_catalog(self, api):
        await api.client.post("/api/models/refresh")
        r = await api.client.delete("/api/models/ollama/qwen3:0.6b")
        assert r.status_code == 200
        assert r.json()["deleted"] is True
        assert "qwen3:0.6b" in api.ollama.deleted

    async def test_delete_unsupported_provider(self, api):
        from tests.conftest import FakePaidProvider
        api.svc.providers_registry.register(FakePaidProvider())
        await api.svc.db.execute(
            "INSERT OR REPLACE INTO models (provider, name, display_name, is_local,"
            " is_free, available, status) VALUES ('paidtest', 'm1', 'm1', 0, 0, 1, 'available')")
        r = await api.client.delete("/api/models/paidtest/m1")
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "UNSUPPORTED_OPERATION"
