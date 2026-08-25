"""Conversations API tests — CRUD, search, pin, archive, favorites."""


async def make(env, content="hello world"):
    r = await env.client.post("/api/chat/completions", json={"content": content})
    from tests.conftest import parse_sse
    return parse_sse(r.text)[0]["conversation_id"]


async def test_create_list_get(api):
    conv = (await api.client.post("/api/conversations", json={"title": "One"})).json()
    assert conv["title"] == "One"
    listed = (await api.client.get("/api/conversations")).json()
    assert any(c["id"] == conv["id"] for c in listed["conversations"])
    loaded = (await api.client.get(f"/api/conversations/{conv['id']}")).json()
    assert loaded["messages"] == []


async def test_rename_pin_favorite_archive(api):
    conv = (await api.client.post("/api/conversations", json={"title": "Two"})).json()
    cid = conv["id"]
    r = await api.client.patch(f"/api/conversations/{cid}",
                               json={"title": "Renamed", "pinned": True,
                                     "favorite": True})
    assert r.json()["title"] == "Renamed"
    assert r.json()["pinned"] is True
    assert r.json()["favorite"] is True

    # pinned sorts first
    await api.client.post("/api/conversations", json={"title": "X"})
    listed = (await api.client.get("/api/conversations")).json()["conversations"]
    assert listed[0]["id"] == cid

    await api.client.patch(f"/api/conversations/{cid}", json={"archived": True})
    active = (await api.client.get("/api/conversations")).json()["conversations"]
    assert all(c["id"] != cid for c in active)
    archived = (await api.client.get("/api/conversations",
                                     params={"archived": True})).json()["conversations"]
    assert any(c["id"] == cid for c in archived)


async def test_search_matches_title_and_content(api):
    await api.client.post("/api/models/refresh")
    cid = await make(api, "tell me about interstellar travel")
    by_content = (await api.client.get("/api/conversations",
                                       params={"query": "interstellar"})).json()
    assert any(c["id"] == cid for c in by_content["conversations"])
    none = (await api.client.get("/api/conversations",
                                 params={"query": "zz-no-match"})).json()
    assert none["count"] == 0


async def test_delete(api):
    conv = (await api.client.post("/api/conversations", json={"title": "Doomed"})).json()
    r = await api.client.delete(f"/api/conversations/{conv['id']}")
    assert r.status_code == 200 and r.json()["deleted"] is True
    assert (await api.client.get(f"/api/conversations/{conv['id']}")).status_code == 404
    again = await api.client.delete(f"/api/conversations/{conv['id']}")
    assert again.status_code == 404


async def test_system_prompt_per_conversation(api):
    conv = (await api.client.post(
        "/api/conversations",
        json={"title": "Sys", "system_prompt": "You are terse."})).json()
    loaded = (await api.client.get(f"/api/conversations/{conv['id']}")).json()
    assert loaded["system_prompt"] == "You are terse."
    r = await api.client.patch(f"/api/conversations/{conv['id']}",
                               json={"system_prompt": "You are verbose."})
    assert r.json()["system_prompt"] == "You are verbose."


async def test_update_404(api):
    r = await api.client.patch("/api/conversations/nope", json={"title": "x"})
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "NOT_FOUND"
