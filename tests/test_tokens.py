"""Token estimation + tracking label integrity (exact vs estimated)."""
from backend.app.services.tokens import estimate_messages, estimate_tokens


def test_estimator_monotonic_and_sane():
    assert estimate_tokens("") == 0
    small = estimate_tokens("hello")
    big = estimate_tokens("hello " * 100)
    assert small >= 1
    assert big > small
    # ~4 chars/token heuristic
    assert abs(estimate_tokens("a" * 400) - 100) <= 2


def test_estimate_messages_adds_overhead():
    msgs = [{"content": "abcd"}, {"content": "abcd"}]
    assert estimate_messages(msgs) > estimate_tokens("abcd") * 2


async def test_tracking_labels_separate_exact_from_estimated(api):
    await api.client.post("/api/models/refresh")
    from tests.conftest import parse_sse

    # fake provider reports counts → exact
    r = await api.client.post("/api/chat/completions", json={"content": "hi"})
    events = parse_sse(r.text)
    usage = next(e for e in events if e["type"] == "usage")
    assert usage["method"] == "exact"

    # provider stops reporting counts → estimated, never masquerading
    async def unknown_counts(model, messages, options, cancel):
        from backend.app.providers.base import StreamChunk
        yield StreamChunk(content="partial reply")
        yield StreamChunk(content="", done=True)  # no counts reported

    api.ollama.chat_stream = unknown_counts
    r = await api.client.post("/api/chat/completions", json={"content": "hi again"})
    events = parse_sse(r.text)
    usage = next(e for e in events if e["type"] == "usage")
    assert usage["method"] == "estimated"
    assert usage["input_tokens"] > 0 and usage["output_tokens"] > 0

    conv_id = events[0]["conversation_id"]
    conv = (await api.client.get(f"/api/conversations/{conv_id}")).json()
    methods = {m["token_method"] for m in conv["messages"]
               if m["role"] == "assistant"}
    assert methods == {"estimated"}
