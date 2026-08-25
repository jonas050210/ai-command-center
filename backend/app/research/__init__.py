"""Research Mode (P6) — REAL and wired.

Web-grounded answers with citations: DuckDuckGo search (ddgs) →
SSRF-guarded, size-capped page fetch → trafilatura extraction → an LLM
answer pass (ModelRouter + CostGuard gated) with numbered sources.
Gated behind the ``network:fetch`` capability; every run is persisted
to the ``research`` table. See routers/research.py for the SSE API.
"""
