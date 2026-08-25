"""Research Mode (P6) — SSRF-guarded web layer + grounded answers.

The web layer is tested against fakes (deterministic DNS, fake ddgs,
fake httpx client) — never the real network. The API flow runs through
the production SSE router + service + repo with only the network edge
(web_search/gather_pages) monkeypatched.
"""
from __future__ import annotations

import socket
import sys
import types
from typing import ClassVar

import pytest

from backend.app.research import web as weblayer
from backend.app.research.web import (
    FETCH_MAX_BYTES,
    PageContent,
    SearchResult,
    UnsafeURLError,
    validate_url,
)
from backend.app.security.permissions import Capability, PermissionPolicy
from tests.conftest import parse_sse
from tests.test_agent import ALL_CAPS

PUBLIC_IP = "93.184.216.34"      # example.com's well-known public address


# ── deterministic DNS ────────────────────────────────────────────────
def fake_dns(monkeypatch, mapping: dict[str, list[str]]):
    """Faithful getaddrinfo: literal IPs resolve without DNS (like the real one)."""
    import ipaddress

    def _getaddrinfo(host, port, *a, **kw):
        try:
            literal = ipaddress.ip_address(host.strip("[]"))
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (str(literal), 0))]
        except ValueError:
            pass
        ips = mapping.get(host)
        if ips is None:
            raise socket.gaierror(f"name or service not known: {host}")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0)) for ip in ips]
    monkeypatch.setattr(socket, "getaddrinfo", _getaddrinfo)


class TestValidateUrl:
    """The SSRF guard is the security boundary of the whole feature."""

    @pytest.mark.parametrize("url", [
        "http://localhost/", "http://127.0.0.1:11434/api/chat",
        "http://[::1]/", "http://0.0.0.0/",
    ])
    def test_loopback_and_unspecified_blocked(self, url):
        with pytest.raises(UnsafeURLError):
            validate_url(url)

    @pytest.mark.parametrize("host", [
        "192.168.1.1", "10.0.0.5", "172.16.0.1", "169.254.169.254",
    ])
    def test_private_and_linklocal_blocked(self, host):
        # literal IPs: no DNS needed, deterministic everywhere
        with pytest.raises(UnsafeURLError):
            validate_url(f"http://{host}/")

    def test_schemes_blocked(self):
        for url in ("ftp://example.com/x", "file:///etc/passwd",
                    "gopher://example.com", "http://"):
            with pytest.raises(UnsafeURLError):
                validate_url(url)

    def test_metadata_endpoint_hostname_blocked(self, monkeypatch):
        fake_dns(monkeypatch, {"metadata.google.internal": ["169.254.169.254"]})
        with pytest.raises(UnsafeURLError):
            validate_url("http://metadata.google.internal/")

    def test_dns_rebinding_single_private_blocked(self, monkeypatch):
        # any one non-public address in the answer → refuse
        fake_dns(monkeypatch, {"evil.example": [PUBLIC_IP, "192.168.0.1"]})
        with pytest.raises(UnsafeURLError):
            validate_url("http://evil.example/")

    def test_unresolvable_blocked(self, monkeypatch):
        fake_dns(monkeypatch, {})
        with pytest.raises(UnsafeURLError):
            validate_url("http://no-such-host.invalid/")

    def test_public_allowed(self, monkeypatch):
        fake_dns(monkeypatch, {"example.com": [PUBLIC_IP]})
        assert validate_url("https://example.com/page").startswith("https://")


# ── web_search (fake ddgs) ───────────────────────────────────────────
class FakeDDGS:
    rows: ClassVar[list] = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def text(self, query, max_results=5):
        return self.rows[:max_results]


def install_fake_ddgs(monkeypatch, rows):
    FakeDDGS.rows = rows
    module = types.ModuleType("ddgs")
    module.DDGS = FakeDDGS
    monkeypatch.setitem(sys.modules, "ddgs", module)


class TestWebSearch:
    async def test_results_mapped(self, monkeypatch):
        install_fake_ddgs(monkeypatch, [
            {"title": "T1", "href": "https://a.example/1", "body": "s1"},
            {"title": "T2", "href": "https://b.example/2", "body": "s2"},
        ])
        out = await weblayer.web_search("q", max_results=5)
        assert [r.url for r in out] == ["https://a.example/1", "https://b.example/2"]
        assert out[0].title == "T1" and out[0].snippet == "s1"

    async def test_max_results_capped(self, monkeypatch):
        install_fake_ddgs(monkeypatch, [
            {"title": f"T{i}", "href": f"https://{i}.example/", "body": "s"}
            for i in range(20)])
        seen = {}

        class Probe(FakeDDGS):
            def text(self, query, max_results=5):
                seen["n"] = max_results
                return []

        module = types.ModuleType("ddgs")
        module.DDGS = Probe
        monkeypatch.setitem(sys.modules, "ddgs", module)
        await weblayer.web_search("q", max_results=50)
        assert seen["n"] == weblayer.SEARCH_MAX_RESULTS


# ── web_fetch (fake httpx client) ────────────────────────────────────
class FakeResponse:
    def __init__(self, status=200, headers=None, body=b""):
        self.status_code = status
        self.headers = headers or {}
        self._body = body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def aiter_bytes(self, n):
        for i in range(0, len(self._body), n):
            yield self._body[i:i + n]


class FakeClient:
    routes: ClassVar[dict] = {}

    def __init__(self, *a, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def stream(self, method, url, follow_redirects=False):
        resp = self.routes[url]
        if isinstance(resp, Exception):
            raise resp
        return resp


def install_fake_http(monkeypatch, routes, dns=None):
    FakeClient.routes = routes
    monkeypatch.setattr(weblayer.httpx, "AsyncClient", FakeClient)
    hosts = dns or {}
    for url in routes:
        host = url.split("/")[2]
        hosts.setdefault(host, [PUBLIC_IP])
    fake_dns(monkeypatch, hosts)


PAGE = (b"<html><head><title>Doc Title</title></head><body><article>"
        b"<p>Substantial article text that extraction can work with, "
        b"long enough to be kept by any reasonable extractor.</p>"
        b"</article></body></html>")


class TestWebFetch:
    async def test_fetch_extracts_text(self, monkeypatch):
        install_fake_http(monkeypatch,
                          {"https://example.com/a": FakeResponse(body=PAGE)})
        page = await weblayer.web_fetch("https://example.com/a")
        assert page.error is None
        assert "Substantial article text" in page.text
        assert page.chars == len(page.text)

    async def test_ssrf_guard_applies_inside_fetch(self, monkeypatch):
        fake_dns(monkeypatch, {"internal.example": ["10.1.2.3"]})
        monkeypatch.setattr(weblayer.httpx, "AsyncClient", FakeClient)
        page = await weblayer.web_fetch("http://internal.example/secret")
        assert page.error and "blocked" in page.error

    async def test_redirect_chain_followed_and_revalidated(self, monkeypatch):
        install_fake_http(monkeypatch, {
            "https://example.com/old": FakeResponse(
                status=301, headers={"location": "/new"}),
            "https://example.com/new": FakeResponse(body=PAGE),
        })
        page = await weblayer.web_fetch("https://example.com/old")
        assert page.error is None and "Substantial article text" in page.text

    async def test_redirect_to_private_blocked(self, monkeypatch):
        install_fake_http(monkeypatch, {
            "https://example.com/lure": FakeResponse(
                status=302, headers={"location": "http://169.254.169.254/meta"}),
        })
        page = await weblayer.web_fetch("https://example.com/lure")
        assert page.error and "blocked" in page.error

    async def test_too_many_redirects(self, monkeypatch):
        routes = {"https://example.com/loop": FakeResponse(
            status=302, headers={"location": "/loop"})}
        install_fake_http(monkeypatch, routes)
        page = await weblayer.web_fetch("https://example.com/loop")
        assert page.error and "redirect" in page.error

    async def test_http_error_status_honest(self, monkeypatch):
        install_fake_http(monkeypatch,
                          {"https://example.com/x": FakeResponse(status=503)})
        page = await weblayer.web_fetch("https://example.com/x")
        assert page.error and "503" in page.error

    async def test_size_cap_enforced(self, monkeypatch):
        big = b"x" * (FETCH_MAX_BYTES + 100_000)
        install_fake_http(monkeypatch,
                          {"https://example.com/big": FakeResponse(body=big)})
        page = await weblayer.web_fetch("https://example.com/big")
        # never held more than the cap (+ one chunk margin) and says so
        assert page.truncated is True

    async def test_gather_pages_bounded(self, monkeypatch):
        install_fake_http(monkeypatch, {
            f"https://example.com/{i}": FakeResponse(body=PAGE) for i in range(6)})
        results = [SearchResult(title=f"t{i}", url=f"https://example.com/{i}",
                                snippet="s") for i in range(6)]
        pages = await weblayer.gather_pages(results, max_pages=4)
        assert len(pages) == 4          # max_pages respected
        assert all(p.error is None for p in pages)


# ── API flow (fake provider, monkeypatched network edge) ─────────────
def patch_web_edge(monkeypatch, results=None, pages=None):
    results = results if results is not None else [
        SearchResult(title="Alpha", url="https://a.example/", snippet="sa"),
        SearchResult(title="Beta", url="https://b.example/", snippet="sb"),
    ]
    pages = pages if pages is not None else [
        PageContent(url="https://a.example/", title="Alpha",
                    text="Alpha content about the topic.", chars=30),
        PageContent(url="https://b.example/", title="Beta",
                    text="Beta content about the topic.", chars=29),
    ]

    async def _search(query, max_results=6):
        return results

    async def _gather(res, max_pages=4):
        return pages

    monkeypatch.setattr("backend.app.research.service.web_search", _search)
    monkeypatch.setattr("backend.app.research.service.gather_pages", _gather)


def by_type(events, t):
    return [e for e in events if e.get("type") == t]


class TestResearchApi:
    async def test_full_flow_with_citations(self, api, monkeypatch):
        patch_web_edge(monkeypatch)
        r = await api.client.post("/api/research/query",
                                  json={"question": "What is the topic?"})
        assert r.status_code == 200, r.text
        events = parse_sse(r.text)
        types_ = [e["type"] for e in events]
        assert types_[0] == "meta" and types_[-1] == "done"
        assert [e["stage"] for e in by_type(events, "status")] == [
            "searching", "fetching", "answering"]
        assert by_type(events, "sources")
        assert by_type(events, "delta")            # answer streamed
        citations = by_type(events, "citations")[0]["citations"]
        assert [c["index"] for c in citations] == [1, 2]
        assert {c["url"] for c in citations} == {
            "https://a.example/", "https://b.example/"}
        done = events[-1]
        assert done["status"] == "complete"
        assert "Hello from a fake local model" in done["answer"]
        # the grounding prompt really carried the sources to the model
        # (FakeOllamaProvider echoes nothing, but usage must be metered)
        usage = by_type(events, "usage")[0]
        assert usage["input_tokens"] > 0 and usage["model"]

    async def test_run_persisted_with_sources(self, api, monkeypatch):
        patch_web_edge(monkeypatch)
        r = await api.client.post("/api/research/query", json={"question": "q1"})
        rid = parse_sse(r.text)[0]["research_id"]
        history = (await api.client.get("/api/research/history")).json()
        assert history["runs"][0]["id"] == rid
        assert history["runs"][0]["status"] == "complete"
        detail = (await api.client.get(f"/api/research/{rid}")).json()["run"]
        assert detail["query"] == "q1"
        assert len(detail["sources"]) == 2
        assert detail["result"]

    async def test_disabled_capability_fails_honestly(self, api, monkeypatch):
        patch_web_edge(monkeypatch)
        await api.svc.settings_service.set("cap_network_fetch", "false")
        r = await api.client.post("/api/research/query", json={"question": "q"})
        events = parse_sse(r.text)
        assert events[0]["type"] == "error"
        assert events[0]["code"] == "RESEARCH_DISABLED"
        # nothing was persisted for a refused run
        history = (await api.client.get("/api/research/history")).json()
        assert history["count"] == 0

    async def test_no_results(self, api, monkeypatch):
        patch_web_edge(monkeypatch, results=[])
        r = await api.client.post("/api/research/query", json={"question": "q"})
        events = parse_sse(r.text)
        err = by_type(events, "error")[0]
        assert err["code"] == "RESEARCH_NO_RESULTS"
        assert events[-1]["status"] == "error"

    async def test_all_fetches_fail_no_hallucination(self, api, monkeypatch):
        patch_web_edge(monkeypatch, pages=[
            PageContent(url="https://a.example/", title="", text="",
                        error="HTTP 503"),
            PageContent(url="https://b.example/", title="", text="",
                        error="no extractable text")])
        r = await api.client.post("/api/research/query", json={"question": "q"})
        events = parse_sse(r.text)
        err = by_type(events, "error")[0]
        assert err["code"] == "RESEARCH_NO_CONTENT"
        assert not by_type(events, "delta")        # never answered from nothing

    async def test_dropped_source_noted(self, api, monkeypatch):
        patch_web_edge(monkeypatch, pages=[
            PageContent(url="https://a.example/", title="Alpha",
                        text="Alpha content.", chars=14),
            PageContent(url="https://b.example/", title="", text="",
                        error="host resolves to a non-public address"),
        ])
        r = await api.client.post("/api/research/query", json={"question": "q"})
        events = parse_sse(r.text)
        notes = by_type(events, "note")
        assert notes and "b.example" in notes[0]["message"]
        citations = by_type(events, "citations")[0]["citations"]
        assert len(citations) == 1 and citations[0]["url"] == "https://a.example/"
        assert events[-1]["status"] == "complete"

    async def test_empty_question_rejected(self, api):
        r = await api.client.post("/api/research/query", json={"question": ""})
        assert r.status_code == 422
        r2 = await api.client.post("/api/research/query", json={"question": "   "})
        events = parse_sse(r2.text)
        assert events[0]["type"] == "error"   # whitespace-only → honest BadRequest
        history = (await api.client.get("/api/research/history")).json()
        assert history["count"] == 0          # refused runs are never persisted

    async def test_stop_unknown_run_404(self, api):
        r = await api.client.post("/api/research/99999/stop")
        assert r.status_code == 404
        assert r.json()["error"]["code"] == "RUN_NOT_FOUND"

    async def test_get_unknown_404(self, api):
        r = await api.client.get("/api/research/424242")
        assert r.status_code == 404
        assert r.json()["error"]["code"] == "RESEARCH_NOT_FOUND"


# ── agent web tools through the gateway ──────────────────────────────
class TestWebAgentTools:
    async def test_web_search_tool(self, tools_env, monkeypatch):
        install_fake_ddgs(monkeypatch, [
            {"title": "T1", "href": "https://a.example/1", "body": "snippet one"},
        ])
        policy = PermissionPolicy(granted=set(ALL_CAPS))
        res = await tools_env.executor.execute(
            "web_search", {"query": "ai news"}, ctx=tools_env.ctx,
            policy=policy, approver=tools_env.approve_all)
        assert res.ok
        assert "https://a.example/1" in res.output and "snippet one" in res.output
        rows = await tools_env.executions.list()
        assert rows[0]["kind"] == "tool:web_search" and rows[0]["status"] == "ok"

    async def test_web_fetch_tool(self, tools_env, monkeypatch):
        install_fake_http(monkeypatch,
                          {"https://example.com/a": FakeResponse(body=PAGE)})
        policy = PermissionPolicy(granted=set(ALL_CAPS))
        res = await tools_env.executor.execute(
            "web_fetch", {"url": "https://example.com/a"}, ctx=tools_env.ctx,
            policy=policy, approver=tools_env.approve_all)
        assert res.ok and "Substantial article text" in res.output

    async def test_web_fetch_tool_ssrf_denied(self, tools_env, monkeypatch):
        fake_dns(monkeypatch, {"internal.example": ["10.9.9.9"]})
        monkeypatch.setattr(weblayer.httpx, "AsyncClient", FakeClient)
        policy = PermissionPolicy(granted=set(ALL_CAPS))
        res = await tools_env.executor.execute(
            "web_fetch", {"url": "http://internal.example/x"}, ctx=tools_env.ctx,
            policy=policy, approver=tools_env.approve_all)
        assert not res.ok and "blocked" in (res.error or "")

    async def test_capability_gate_denies_and_audits(self, tools_env, monkeypatch):
        install_fake_ddgs(monkeypatch, [
            {"title": "T", "href": "https://a.example/", "body": "s"}])
        policy = PermissionPolicy(granted={Capability.FILESYSTEM_READ})
        res = await tools_env.executor.execute(
            "web_search", {"query": "q"}, ctx=tools_env.ctx,
            policy=policy, approver=tools_env.approve_all)
        assert not res.ok and "not granted" in (res.error or "")
        rows = await tools_env.executions.list()
        assert rows[0]["kind"] == "tool:web_search" and rows[0]["status"] == "denied"
