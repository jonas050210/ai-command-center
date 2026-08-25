"""Startup, health, settings, providers + NOT IMPLEMENTED boundaries."""


async def test_health(api):
    r = await api.client.get("/api/health")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert data["db"] == "ok"
    assert data["ollama"]["status"] == "running"
    assert data["version"]


async def test_health_ollama_unavailable_detection(api):
    api.ollama.running = False
    r = await api.client.get("/api/health")
    data = r.json()
    assert data["ollama"]["status"] == "unavailable"
    assert data["ollama"]["detail"]


async def test_system_status(api):
    r = await api.client.get("/api/system/status")
    assert r.status_code == 200
    data = r.json()
    assert data["runtime"]["default_model"] == api.settings.default_model
    assert data["runtime"]["free_only"] is True
    assert data["metrics"]["uptime_s"] >= 0


async def test_settings_get_and_update(api):
    data = (await api.client.get("/api/settings")).json()
    assert data["free_only"] is True
    assert data["max_spend"] == 0.0
    assert data["default_model"] == api.settings.default_model

    r = await api.client.put("/api/settings", json={"custom_instructions": "Be brief.",
                                                    "num_ctx": 4096})
    assert r.status_code == 200
    assert r.json()["custom_instructions"] == "Be brief."
    assert r.json()["num_ctx"] == 4096
    # persisted
    again = (await api.client.get("/api/settings")).json()
    assert again["num_ctx"] == 4096


async def test_settings_validation(api):
    r = await api.client.put("/api/settings", json={"num_ctx": 10})
    assert r.status_code == 422  # pydantic bound (>=512)
    r = await api.client.put("/api/settings", json={"max_spend": -5})
    assert r.status_code == 422


async def test_providers_endpoint(api):
    r = await api.client.get("/api/providers")
    assert r.status_code == 200
    providers = r.json()["providers"]
    ollama = next(p for p in providers if p["name"] == "ollama")
    assert ollama["is_local"] is True
    assert ollama["is_free"] is True
    assert ollama["status"] == "running"


async def test_future_features_return_501_not_implemented(api):
    for path in ("/api/agent/run", "/api/team/start", "/api/research/query",
                 "/api/git/status"):
        r = await api.client.get(path)
        assert r.status_code == 501, path
        body = r.json()
        assert body["error"]["code"] == "NOT_IMPLEMENTED"
        assert "NOT IMPLEMENTED" in body["error"]["message"]
        r2 = await api.client.post(path, json={})
        assert r2.status_code == 501


async def test_unknown_api_route_shape(api):
    r = await api.client.get("/api/does-not-exist")
    assert r.status_code == 404
