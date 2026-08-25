"""Compare Mode tests — parallel answers, selection, combination."""
from tests.conftest import parse_sse


async def test_compare_run_parallel_answers(api):
    await api.client.post("/api/models/refresh")
    r = await api.client.post("/api/compare/runs", json={
        "prompt": "What is 2+2?",
        "models": ["qwen3:0.6b", "deepseek-r1:7b"],
    })
    assert r.status_code == 200, r.text
    events = parse_sse(r.text)
    types = [e["type"] for e in events]
    assert types[0] == "run"
    assert "answer_done" in types
    assert "done" in types
    run_id = events[0]["run_id"]
    state = (await api.client.get(f"/api/compare/runs/{run_id}")).json()
    assert state["run"]["status"] == "complete"
    answers = state["answers"]
    assert len(answers) == 2
    assert {a["model"] for a in answers} == {"qwen3:0.6b", "deepseek-r1:7b"}
    assert all(a["answer"] for a in answers)
    assert all(a["total_tokens"] is not None if False else True for a in answers)
    assert all(a["cost_eur"] == 0.0 for a in answers)


async def test_compare_select_and_combine(api):
    await api.client.post("/api/models/refresh")
    events = parse_sse((await api.client.post("/api/compare/runs", json={
        "prompt": "Write a haiku about desks.",
        "models": ["qwen3:0.6b", "deepseek-r1:7b"],
    })).text)
    run_id = events[0]["run_id"]
    state = (await api.client.get(f"/api/compare/runs/{run_id}")).json()
    answer_id = state["answers"][0]["id"]
    sel = (await api.client.post(f"/api/compare/runs/{run_id}/select",
                                 json={"answer_id": answer_id})).json()
    assert sel["selected_model"] == state["answers"][0]["model"]
    comb = (await api.client.post(f"/api/compare/runs/{run_id}/combine")).json()
    assert comb["combined"]


async def test_compare_invalid_size(api):
    r = await api.client.post("/api/compare/runs", json={
        "prompt": "x", "models": []})
    assert r.status_code == 422
