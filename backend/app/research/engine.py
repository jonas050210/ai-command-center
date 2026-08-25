"""Research Mode — multi-source web research with citations.

Only real sources are ever stored: every item in ``sources_json`` comes
from an actual search/fetch. If the search provider is unreachable (or
unconfigured), the run fails with an explicit error — nothing is
fabricated. Optional synthesis (notes, summary, comparison) is written by
a local model when one is available and clearly labelled otherwise.

Search providers (configurable in settings): ``duckduckgo`` (default,
HTML-based, no API key) or ``disabled``.
"""
from __future__ import annotations

import asyncio
import html
import json
import logging
import re
from typing import Any, AsyncIterator, Callable

import httpx

from ..core.errors import BadRequest, NotFound
from ..db.repo import ResearchRepo
from ..providers.base import ChatMessage
from ..services.model_runner import ModelRunner
from ..services.settings_service import SettingsService

log = logging.getLogger("aicc.research")

MAX_SOURCES = 8
MAX_FETCH_BYTES = 2_000_000
MAX_EXCERPT = 8_000
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

SearchFn = Callable[[str, int], Any]


class ResearchEngine:
    def __init__(self, *, research: ResearchRepo, runner: ModelRunner,
                 settings: SettingsService, search_fn: SearchFn | None = None):
        self.research = research
        self.runner = runner
        self.settings = settings
        self._search_fn = search_fn or self._search_duckduckgo

    # ── providers ────────────────────────────────────────────────────
    async def _search_duckduckgo(self, query: str, limit: int) -> list[dict[str, Any]]:
        url = "https://lite.duckduckgo.com/lite/"
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True,
                                     headers={"User-Agent": USER_AGENT}) as client:
            r = await client.get(url, params={"q": query})
            r.raise_for_status()
        return self._parse_lite_ddg(r.text, limit)

    @staticmethod
    def _parse_lite_ddg(text: str, limit: int) -> list[dict[str, Any]]:
        """Parse DuckDuckGo Lite HTML: links + snippets. Real data only."""
        link_re = re.compile(
            r'<a[^>]+class="result-link"[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
            re.S)
        snippet_re = re.compile(
            r'<td[^>]*class="result-snippet"[^>]*>(.*?)</td>', re.S)
        links = link_re.findall(text)
        snippets = [re.sub(r"<[^>]+>", "", s) for s in snippet_re.findall(text)]
        out: list[dict[str, Any]] = []
        for i, (href, title) in enumerate(links[:limit]):
            title = html.unescape(re.sub(r"<[^>]+>", "", title)).strip()
            url = html.unescape(href.strip())
            snippet = html.unescape(snippets[i]).strip() if i < len(snippets) else ""
            if url.startswith("//"):
                url = "https:" + url
            if not url.lower().startswith(("http://", "https://")):
                continue
            out.append({"title": title or url, "url": url, "snippet": snippet[:500]})
        return out

    @staticmethod
    async def _fetch_text(url: str) -> str:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True,
                                     headers={"User-Agent": USER_AGENT}) as client:
            r = await client.get(url)
            r.raise_for_status()
            content = r.content[:MAX_FETCH_BYTES]
        text = content.decode("utf-8", errors="replace")
        text = re.sub(r"(?is)<(script|style|nav|footer|header)[^>]*>.*?</\1>", " ", text)
        text = re.sub(r"(?s)<[^>]+>", " ", text)
        text = html.unescape(text)
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n\s*\n+", "\n\n", text).strip()
        return text[:MAX_EXCERPT]

    # ── engine ───────────────────────────────────────────────────────
    async def run(self, *, query: str, project_id: int | None = None,
                  synthesize: bool = True,
                  provider_name: str | None = None,
                  model_name: str | None = None,
                  cancel=None) -> AsyncIterator[dict[str, Any]]:
        query = query.strip()
        if not query:
            raise BadRequest("Research query must not be empty.")
        provider = str(await self.settings.get_typed("search_engine") or
                       "duckduckgo").lower()
        if provider == "disabled":
            raise BadRequest("Research is disabled. Enable a search engine in "
                             "Settings (search_engine).", code="RESEARCH_DISABLED")
        limit = min(int(await self.settings.get_typed("research_max_sources") or 5),
                    MAX_SOURCES)
        row = await self.research.create(query, project_id)
        rid = row["id"]
        yield {"type": "run", "research_id": rid}

        try:
            yield {"type": "status", "research_id": rid, "status": "searching",
                   "message": f"Searching ({provider})…"}
            sources = await self._search_fn(query, limit)
            if not sources:
                await self.research.finish(
                    rid, status="no_results", result="", sources_json="[]",
                    summary="No results found. Search provider returned nothing — "
                            "no sources were invented.")
                yield {"type": "status", "research_id": rid, "status": "no_results",
                       "message": "No results found."}
                yield {"type": "done", "research_id": rid, "status": "no_results"}
                return

            fetched: list[dict[str, Any]] = []
            for i, src in enumerate(sources):
                if cancel is not None and cancel.is_set():
                    break
                yield {"type": "source", "research_id": rid, "index": i,
                       "title": src["title"], "url": src["url"],
                       "snippet": src["snippet"][:300]}
                try:
                    await asyncio.sleep(0)  # yield between fetches
                    body = await self._fetch_text(src["url"])
                    fetched.append({**src, "excerpt": body})
                except Exception as exc:
                    log.debug("source fetch failed: %s (%s)", src["url"], exc)
                    fetched.append({**src, "excerpt": ""})

            notes = ""
            if fetched:
                notes = "\n\n".join(
                    f"[{i + 1}] {s['title']}\n{s['url']}\n"
                    f"{s.get('snippet', '')}\n"
                    f"{s.get('excerpt', '')[:800]}"
                    for i, s in enumerate(fetched[:5]))[:12000]
            summary, comparison = "", ""

            if synthesize:
                yield {"type": "status", "research_id": rid, "status": "synthesizing",
                       "message": "Synthesizing with local model…"}
                summary, comparison = await self._synthesize(
                    query, fetched, provider_name, model_name)
            else:
                summary = "Synthesis skipped (synthesize=false)."

            sources_json = json.dumps(fetched, ensure_ascii=False)
            result = self._compose_markdown(query, fetched, notes, summary,
                                            comparison)
            await self.research.finish(rid, status="complete", result=result,
                                       sources_json=sources_json, notes=notes,
                                       summary=summary, comparison=comparison)
            yield {"type": "summary", "research_id": rid, "summary": summary}
            yield {"type": "done", "research_id": rid, "status": "complete",
                   "sources": len(fetched)}
        except Exception as exc:
            log.warning("research run %s failed: %s", rid, exc)
            await self.research.finish(rid, status="error", result="",
                                       sources_json="[]",
                                       summary=f"Research failed: {exc}")
            yield {"type": "error", "research_id": rid, "code": "RESEARCH_FAILED",
                   "message": str(exc)}

    async def _synthesize(self, query: str, sources: list[dict],
                          provider_name: str | None,
                          model_name: str | None) -> tuple[str, str]:
        source_lines = "\n".join(
            f"[{i + 1}] {s['title']} — {s['url']} — {(s.get('excerpt') or '')[:600]}"
            for i, s in enumerate(sources[:5]))
        try:
            gen = await self.runner.generate(
                messages=[ChatMessage(role="user", content=(
                    f"Research question: {query}\n\nSources:\n{source_lines}\n\n"
                    "Write: 1) SUMMARY of what the sources say (150-250 words, "
                    "plain text with [n] citations), 2) COMPARISON: note "
                    "consensus/conflicts between sources (max 120 words). "
                    "Use only the provided sources."))],
                provider_name=provider_name, model_name=model_name)
        except Exception as exc:
            return (f"Synthesis unavailable: {exc}", "")
        text = (gen.text or "").strip()
        if not text:
            return "Synthesis unavailable: model returned no content.", ""
        comp = ""
        if "COMPARISON" in text.upper():
            head, _, tail = text.partition("COMPARISON")
            summary = head.replace("SUMMARY", "", 1).strip()
            comp = tail.strip()
            return summary[:3000], comp[:2000]
        return text[:3000], ""

    @staticmethod
    def _compose_markdown(query: str, sources: list[dict], notes: str,
                          summary: str, comparison: str) -> str:
        lines = [f"# Research: {query}", ""]
        lines.append("## Sources")
        for i, s in enumerate(sources):
            lines.append(f"{i + 1}. **{s['title']}** — {s['url']}")
        lines += ["", "## Summary", summary or "(none)"]
        if comparison:
            lines += ["", "## Comparison", comparison]
        lines += ["", "## Notes", notes or "(none)", ""]
        return "\n".join(lines)

    async def export_markdown(self, rid: int) -> str:
        row = await self.research.get(rid)
        if row is None:
            raise NotFound(f"Research '{rid}' not found.")
        return row["result"] or self._compose_markdown(
            row["query"], json.loads(row["sources_json"] or "[]"),
            row["notes"], row["summary"], row["comparison"])
