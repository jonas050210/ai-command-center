"""Model Center API tests — discovery, catalog, filters, favorites, testing."""


async def test_refresh_syncs_real_models(api):
    r = await api.client.post("/api/models/refresh")
    assert r.status_code == 200
    data = r.json()
    assert data["results"]["ollama"]["synced"] == 2
    names = set(data["results"]["ollama"]["models"])
    assert names == {"qwen3:0.6b", "deepseek-r1:7b"}


async def test_model_center_lists_real_fields(api):
    await api.client.post("/api/models/refresh")
    data = (await api.client.get("/api/models")).json()
    assert data["count"] == 2
    qwen = next(m for m in data["models"] if m["name"] == "qwen3:0.6b")
    assert qwen["provider"] == "ollama"
    assert qwen["location"] == "local"
    assert qwen["is_free"] is True
    assert qwen["context_length"] == 40960      # from /api/show enrichment
    assert qwen["size_bytes"] == 522_000_000
    assert qwen["parameter_size"] == "0.6B"
    assert qwen["status"] == "available"
    assert set(qwen["categories"]) >= {"general", "local", "free", "fast"}
    r1 = next(m for m in data["models"] if m["name"] == "deepseek-r1:7b")
    assert "reasoning" in r1["categories"]
    assert "fast" not in r1["categories"]


async def test_model_search_and_filters(api):
    await api.client.post("/api/models/refresh")
    by_q = (await api.client.get("/api/models", params={"q": "deepseek"})).json()
    assert by_q["count"] == 1
    by_cat = (await api.client.get("/api/models",
                                   params={"category": "reasoning"})).json()
    assert by_cat["count"] == 1 and by_cat["models"][0]["name"] == "deepseek-r1:7b"
    by_size = (await api.client.get("/api/models", params={"sort": "size"})).json()
    assert by_size["models"][0]["name"] == "deepseek-r1:7b"  # bigger first


async def test_favorites(api):
    await api.client.post("/api/models/refresh")
    r = await api.client.post("/api/models/ollama/qwen3:0.6b/favorite",
                              json={"favorite": True})
    assert r.status_code == 200 and r.json()["favorite"] is True
    favs = (await api.client.get("/api/models", params={"favorites": True})).json()
    assert favs["count"] == 1
    await api.client.post("/api/models/ollama/qwen3:0.6b/favorite",
                          json={"favorite": False})
    favs = (await api.client.get("/api/models", params={"favorites": True})).json()
    assert favs["count"] == 0


async def test_favorite_unknown_model_404(api):
    r = await api.client.post("/api/models/ollama/nope/favorite",
                              json={"favorite": True})
    assert r.status_code == 404


async def test_model_speed_test_records_exact_measurement(api):
    await api.client.post("/api/models/refresh")
    r = await api.client.post("/api/models/test",
                              json={"provider": "ollama", "name": "qwen3:0.6b"})
    assert r.status_code == 200
    result = r.json()
    assert result["token_method"] == "exact"
    assert result["output_tokens"] > 0
    assert result["tokens_per_second"] and result["tokens_per_second"] > 0
    assert result["cost_eur"] == 0.0
    # speed now visible on the model card
    data = (await api.client.get("/api/models")).json()
    qwen = next(m for m in data["models"] if m["name"] == "qwen3:0.6b")
    assert qwen["measured_tps"] is not None
    assert qwen["usage_count"] >= 1


async def test_recently_used_after_chat(api):
    await api.client.post("/api/models/refresh")
    await api.client.post("/api/chat/completions", json={"content": "hello"})
    data = (await api.client.get("/api/models")).json()
    assert len(data["recent"]) == 1
    assert data["recent"][0]["name"] == api.settings.default_model


async def test_delete_model(api):
    await api.client.post("/api/models/refresh")
    r = await api.client.delete("/api/models/ollama/deepseek-r1:7b")
    assert r.status_code == 200 and r.json()["deleted"] is True
    assert "deepseek-r1:7b" in api.ollama.deleted
    data = (await api.client.get("/api/models")).json()
    assert data["count"] == 1


async def test_refresh_handles_unavailable_provider(api):
    api.ollama.running = False

    async def boom():
        from backend.app.core.errors import ProviderUnavailable
        raise ProviderUnavailable("Ollama is unavailable.")

    api.ollama.list_models = boom
    r = await api.client.post("/api/models/refresh")
    assert r.status_code == 200
    assert "error" in r.json()["results"]["ollama"]
