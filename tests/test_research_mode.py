"""Research Mode tests — real search, honest failure, export, no fabrication."""
from tests.conftest import parse_sse


async def test_research_run_with_fake_search(api, monkeypatch):
    """Inject a fake search provider; everything downstream must be real."""
    async def fake_search(query, limit):
        return [
            {"title": "Fake Source One", "url": "https://example.com/one",
             "snippet": "first result about " + query},
            {"title": "Fake Source Two", "url": "https://example.org/two",
             "snippet": "second result about " + query},
        ]

    async def fake_fetch(url):
        return "Excerpt text for " + url

    engine = api.svc.research
    engine._search_fn = fake_search
    engine._fetch_text = staticmethod(fake_fetch)

    r = await api.client.post("/api/research/runs", json={
        "query": "local-first AI architecture", "synthesize": True})
    assert r.status_code == 200, r.text
    events = parse_sse(r.text)
    assert events[0]["type"] == "run"
    assert any(e["type"] == "source" for e in events)
    assert any(e["type"] == "summary" for e in events)
    assert events[-1]["type"] == "done"
    rid = events[0]["research_id"]

    state = (await api.client.get(f"/api/research/runs/{rid}")).json()
    assert state["status"] == "complete"
    sources = state["sources"]
    assert len(sources) == 2
    assert all(s["url"].startswith("http") for s in sources)
    # never fabricated: every source has a real url from the search provider
    assert {s["url"] for s in sources} == {"https://example.com/one",
                                            "https://example.org/two"}
    assert state["summary"]
    # citations in the result use the real source indexes
    assert "[1]" in state["result"] or "[2]" in state["result"]

    md = (await api.client.get(f"/api/research/runs/{rid}/export")).text
    assert "https://example.com/one" in md


async def test_research_no_results_is_honest(api, monkeypatch):
    async def no_results(query, limit):
        return []

    engine = api.svc.research
    engine._search_fn = no_results
    r = await api.client.post("/api/research/runs", json={
        "query": "nothing here", "synthesize": True})
    events = parse_sse(r.text)
    assert events[-1]["type"] == "done"
    rid = events[0]["research_id"]
    state = (await api.client.get(f"/api/research/runs/{rid}")).json()
    assert state["status"] == "no_results"
    assert state["sources"] == []
    assert "no results" in state["summary"].lower()


async def test_research_error_is_honest(api, monkeypatch):
    async def boom(query, limit):
        raise RuntimeError("search provider unreachable")

    engine = api.svc.research
    engine._search_fn = boom
    r = await api.client.post("/api/research/runs", json={
        "query": "x", "synthesize": False})
    events = parse_sse(r.text)
    assert events[-1]["type"] == "error"
    rid = events[0]["research_id"]
    state = (await api.client.get(f"/api/research/runs/{rid}")).json()
    assert state["status"] == "error"
    assert state["sources"] == []


async def test_research_disabled(api, monkeypatch):
    await api.client.put("/api/settings", json={"search_engine": "disabled"})
    r = await api.client.post("/api/research/runs", json={"query": "x"})
    events = parse_sse(r.text)
    assert events[-1]["type"] == "error"
    assert "disabled" in events[-1]["message"].lower()


async def test_research_delete(api, monkeypatch):
    async def one_source(query, limit):
        return [{"title": "T", "url": "https://example.com/x", "snippet": "s"}]

    engine = api.svc.research
    engine._search_fn = one_source
    engine._fetch_text = staticmethod(lambda url: "body")
    events = parse_sse((await api.client.post("/api/research/runs",
                                              json={"query": "q"})).text)
    rid = events[0]["research_id"]
    r = await api.client.delete(f"/api/research/runs/{rid}")
    assert r.status_code == 200
    assert (await api.client.get(f"/api/research/runs/{rid}")).status_code == 404
