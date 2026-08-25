"""Database foundation tests: migrations + CRUD across the schema."""
from backend.app.db.migrations import migrate
from backend.app.db.repo import (ConversationsRepo, MessagesRepo, ModelsRepo,
                                 ProvidersRepo, SettingsRepo, UsageRepo,
                                 ExecutionsRepo)


async def test_migrations_apply_once(db):
    applied = await migrate(db)  # already applied in fixture → nothing new
    assert applied == []
    tables = {r["name"] for r in await db.fetchall(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    expected = {"conversations", "messages", "models", "providers", "projects",
                "tasks", "teams", "team_members", "usage_events", "files",
                "executions", "research", "settings", "credentials",
                "schema_migrations"}
    assert expected <= tables


async def test_db_health(db):
    assert await db.health() is True


async def test_conversation_message_crud(db):
    convs, msgs = ConversationsRepo(db), MessagesRepo(db)
    conv = await convs.create("Test chat", "qwen3:0.6b", "ollama", "Be brief.")
    assert conv["title"] == "Test chat"
    m1 = await msgs.create(conv["id"], "user", "hello")
    m2 = await msgs.create(conv["id"], "assistant", "hi", model="qwen3:0.6b",
                           provider="ollama")
    await msgs.finalize(m2["id"], content="hi there", status="complete",
                        input_tokens=10, output_tokens=5, method="exact")
    loaded = await msgs.list_for(conv["id"])
    assert len(loaded) == 2
    assert loaded[1]["token_method"] == "exact"
    assert loaded[1]["input_tokens"] == 10

    await convs.add_tokens(conv["id"], 10, 5)
    conv2 = await convs.get(conv["id"])
    assert conv2["total_output_tokens"] == 5

    await convs.update(conv["id"], pinned=True, favorite=True, title="Renamed")
    conv3 = await convs.get(conv["id"])
    assert conv3["pinned"] == 1 and conv3["favorite"] == 1 and conv3["title"] == "Renamed"

    # search matches message content
    results = await convs.list(q="hi there")
    assert any(r["id"] == conv["id"] for r in results)

    await convs.delete(conv["id"])
    assert await convs.get(conv["id"]) is None
    assert await msgs.list_for(conv["id"]) == []  # cascade


async def test_archive_filter(db):
    convs = ConversationsRepo(db)
    a = await convs.create("active", None, None, None)
    b = await convs.create("archived", None, None, None)
    await convs.update(b["id"], archived=True)
    active = await convs.list(archived=False)
    assert any(r["id"] == a["id"] for r in active)
    assert all(r["id"] != b["id"] for r in active)
    archived = await convs.list(archived=True)
    assert any(r["id"] == b["id"] for r in archived)


async def test_models_upsert_and_usage(db):
    models, providers = ModelsRepo(db), ProvidersRepo(db)
    await providers.upsert("ollama", "Ollama", True, "http://localhost:11434")
    row = {"provider": "ollama", "name": "qwen3:0.6b", "display_name": "qwen3:0.6b",
           "is_local": True, "is_free": True, "context_length": 40960,
           "size_bytes": 522_000_000, "parameter_size": "0.6B",
           "quantization": "Q4_K_M", "family": "qwen3", "families": ["qwen3"],
           "capabilities": ["completion"], "categories": ["general", "local", "free", "fast"],
           "available": True, "status": "available", "raw": {}}
    await models.upsert_from_provider(row)
    await models.upsert_from_provider(row)  # idempotent
    listed = await models.list()
    assert len(listed) == 1
    assert listed[0]["context_length"] == 40960

    await models.record_usage("ollama", "qwen3:0.6b", 100, 50, 42.0)
    m = await models.get("ollama", "qwen3:0.6b")
    assert m["total_input_tokens"] == 100
    assert m["measured_tps"] == 42.0
    assert m["usage_count"] == 1

    await models.mark_missing("ollama", ["other-model"])
    m = await models.get("ollama", "qwen3:0.6b")
    assert m["available"] == 0 and m["status"] == "unavailable"


async def test_usage_ledger(db):
    usage = UsageRepo(db)
    await usage.record(conversation_id="c1", message_id="m1", model="qwen3:0.6b",
                       provider="ollama", input_tokens=10, output_tokens=5,
                       method="exact", cost_eur=0.0)
    totals = await usage.totals()
    assert totals["input_tokens"] == 10
    assert totals["output_tokens"] == 5
    assert totals["cost_eur"] == 0.0
    per_model = await usage.per_model()
    assert per_model[0]["model"] == "qwen3:0.6b"


async def test_settings_repo(db):
    repo = SettingsRepo(db)
    assert await repo.get("free_only") is None
    await repo.set("free_only", "true")
    await repo.set("free_only", "false")
    assert await repo.get("free_only") == "false"


async def test_executions_log(db):
    repo = ExecutionsRepo(db)
    eid = await repo.log(kind="tool", status="success", command="pytest -q",
                         actor="user", exit_code=0, log_text="ok")
    rows = await repo.list()
    assert rows[0]["id"] == eid
    assert rows[0]["actor"] == "user"
