"""Web access layer (P6) — search + safe page extraction.

Security invariants (this is the ONLY outbound HTTP for content):
* http/https only; explicit redirects (no silent follow), each hop
  revalidated — max 3;
* SSRF guard: the hostname is resolved and private/loopback/link-local/
  reserved addresses are refused, including alternate notations;
* response size is hard-capped (never reads a huge body into memory);
* timeouts are fixed and short; failures return honest errors, never
  half-fetched content.
"""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

import httpx

from ..config import APP_VERSION

log = logging.getLogger("aicc.web")

SEARCH_MAX_RESULTS = 8
FETCH_TIMEOUT_S = 12.0
FETCH_MAX_BYTES = 900_000
FETCH_MAX_CHARS = 80000
TEXT_CAP_PER_SOURCE = 3500
MAX_REDIRECTS = 3
USER_AGENT = (f"AICommandCenter/{APP_VERSION} (local research tool; "
              "+https://github.com/jonas050210/ai-command-center)")

_SAFE_SCHEMES = {"http", "https"}


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str


@dataclass
class PageContent:
    url: str
    title: str
    text: str
    chars: int = 0
    error: str | None = None
    truncated: bool = field(default=False)


class UnsafeURLError(Exception):
    pass


def _host_addresses(host: str) -> set[ipaddress._BaseAddress]:
    infos = socket.getaddrinfo(host, None)
    return {ipaddress.ip_address(i[4][0]) for i in infos}


def _is_public_addr(addr: ipaddress._BaseAddress) -> bool:
    return not (addr.is_private or addr.is_loopback or addr.is_link_local
                or addr.is_multicast or addr.is_reserved
                or addr.is_unspecified)


def validate_url(url: str) -> str:
    """Raise UnsafeURLError unless the URL is a public http(s) address."""
    try:
        parsed = urlparse(url.strip())
    except Exception as exc:
        raise UnsafeURLError(f"unparseable URL: {exc}") from exc
    if parsed.scheme not in _SAFE_SCHEMES:
        raise UnsafeURLError(f"scheme '{parsed.scheme}' not allowed (http/https only)")
    host = parsed.hostname
    if not host:
        raise UnsafeURLError("URL has no host")
    try:
        addrs = _host_addresses(host)
    except socket.gaierror as exc:
        raise UnsafeURLError(f"cannot resolve host '{host}'") from exc
    for addr in addrs:
        if not _is_public_addr(addr):
            raise UnsafeURLError(
                f"host '{host}' resolves to a non-public address ({addr}) — blocked")
    return parsed.geturl()


async def web_search(query: str, max_results: int = 5) -> list[SearchResult]:
    """DuckDuckGo text search via ddgs (sync lib → thread)."""
    from ddgs import DDGS  # deferred import: optional at runtime

    def _run() -> list[SearchResult]:
        out: list[SearchResult] = []
        with DDGS() as ddgs:
            for r in ddgs.text(query, max_results=max(1, min(max_results, SEARCH_MAX_RESULTS))):
                out.append(SearchResult(
                    title=(r.get("title") or "").strip(),
                    url=(r.get("href") or "").strip(),
                    snippet=(r.get("body") or "").strip()))
        return out

    return await asyncio.to_thread(_run)


async def _fetch_raw(client: httpx.AsyncClient, url: str) -> tuple[bytes, str, bool]:
    """GET with manual, validated redirect chain + hard size cap.

    Returns (body_prefix, content_type, hit_size_cap) — the cap flag is
    reported honestly instead of silently serving a cut page.
    """
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        validate_url(current)
        async with client.stream("GET", current, follow_redirects=False) as resp:
            if resp.status_code in (301, 302, 303, 307, 308):
                location = resp.headers.get("location")
                if not location:
                    raise UnsafeURLError(f"redirect without Location from {current}")
                current = urljoin(current, location)
                continue
            if resp.status_code != 200:
                raise UnsafeURLError(f"HTTP {resp.status_code} from {current}")
            ctype = resp.headers.get("content-type", "")
            chunks: list[bytes] = []
            size = 0
            over_cap = False
            async for chunk in resp.aiter_bytes(16384):
                size += len(chunk)
                if size > FETCH_MAX_BYTES:
                    over_cap = True
                    break
                chunks.append(chunk)
            return b"".join(chunks), ctype, over_cap
    raise UnsafeURLError(f"too many redirects (>{MAX_REDIRECTS})")


def _extract(html: bytes) -> tuple[str, str]:
    import trafilatura  # deferred import: optional at runtime
    text = trafilatura.extract(
        html, include_comments=False, include_tables=False,
        no_fallback=False, favor_recall=False) or ""
    title = ""
    try:
        from trafilatura.metadata import extract_metadata
        meta = extract_metadata(html)
        title = (meta.title if meta else "") or ""
    except Exception:
        title = ""
    return text.strip(), title.strip()


async def web_fetch(url: str, timeout: float = FETCH_TIMEOUT_S) -> PageContent:
    """Fetch one public page and extract readable text. Errors are honest."""
    try:
        async with httpx.AsyncClient(
                headers={"User-Agent": USER_AGENT,
                         "Accept": "text/html,application/xhtml+xml,*/*;q=0.8"},
                timeout=httpx.Timeout(timeout)) as client:
            raw, _ctype, over_cap = await _fetch_raw(client, url)
        text, title = await asyncio.to_thread(_extract, raw)
        if not text:
            # fall back to a raw-text hint when extraction finds nothing useful
            try:
                text = raw[:8000].decode("utf-8", errors="replace")
            except Exception:
                text = ""
        truncated = over_cap
        if len(text) > FETCH_MAX_CHARS:
            text = text[:FETCH_MAX_CHARS]
            truncated = True
        return PageContent(url=url, title=title, text=text,
                           chars=len(text), truncated=truncated)
    except UnsafeURLError as exc:
        return PageContent(url=url, title="", text="", error=str(exc))
    except (httpx.HTTPError, ValueError, OSError) as exc:
        return PageContent(url=url, title="", text="",
                           error=f"fetch failed: {type(exc).__name__}: {exc}")


async def gather_pages(results: list[SearchResult],
                       max_pages: int = 4) -> list[PageContent]:
    """Fetch top sources concurrently (bounded) with per-page caps."""
    sem = asyncio.Semaphore(4)

    async def one(r: SearchResult) -> PageContent:
        async with sem:
            return await web_fetch(r.url)

    return await asyncio.gather(*(one(r) for r in results[:max_pages]))
