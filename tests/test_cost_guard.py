"""CostGuard — the strict €0 spending protection (HARD REQUIREMENT)."""
import pytest

from backend.app.core.errors import BudgetExceeded, PaidModelBlocked
from backend.app.services.cost_guard import PAID_BLOCKED_MESSAGE
from tests.conftest import FakePaidProvider


async def test_free_local_model_allowed(services_env):
    env = services_env
    # should not raise — Ollama is €0.00
    await env.guard.guard_request(env.ollama, "qwen3:0.6b", None, total_spent_eur=0.0)


PAID_ROW = {"provider": "paidtest", "name": "paid-model-1", "display_name": "Paid",
            "is_local": False, "is_free": False, "cost_input_per_mtok": 5.0,
            "cost_output_per_mtok": 15.0, "context_length": None, "size_bytes": None,
            "parameter_size": None, "quantization": None, "family": None,
            "families": [], "capabilities": [], "categories": [],
            "available": True, "status": "available", "raw": {}}


async def seed_paid_row(env):
    """Unsynced cloud models are fail-closed — these tests use the
    authoritative synced catalog row for pricing instead."""
    await env.models.upsert_from_provider(dict(PAID_ROW))
    return await env.models.get("paidtest", "paid-model-1")


async def test_paid_model_blocked_with_exact_message(services_env):
    paid = FakePaidProvider()
    row = await seed_paid_row(services_env)
    with pytest.raises(PaidModelBlocked) as exc:
        await services_env.guard.guard_request(paid, "paid-model-1", row,
                                               total_spent_eur=0.0)
    assert exc.value.message == PAID_BLOCKED_MESSAGE
    assert exc.value.code == "PAID_MODEL_BLOCKED"
    assert exc.value.status_code == 403
    assert "Free-only mode is enabled" in exc.value.message
    assert "No money was spent" in exc.value.message


async def test_paid_model_blocked_via_db_row_pricing(services_env):
    """Pricing from the synced models table is authoritative."""
    env = services_env
    paid = FakePaidProvider()
    row = {"provider": "paidtest", "name": "paid-model-1", "display_name": "Paid",
           "is_local": False, "is_free": False, "cost_input_per_mtok": 5.0,
           "cost_output_per_mtok": 15.0, "context_length": None, "size_bytes": None,
           "parameter_size": None, "quantization": None, "family": None,
           "families": [], "capabilities": [], "categories": [],
           "available": True, "status": "available", "raw": {}}
    await env.models.upsert_from_provider(row)
    saved = await env.models.get("paidtest", "paid-model-1")
    with pytest.raises(PaidModelBlocked):
        await env.guard.guard_request(paid, "paid-model-1", saved, total_spent_eur=0.0)


async def test_budget_exceeded_when_free_only_off(services_env):
    env = services_env
    paid = FakePaidProvider()
    row = await seed_paid_row(env)
    await env.settings_service.set("free_only", False)
    await env.settings_service.set("max_spend", 0.0)
    with pytest.raises(BudgetExceeded) as exc:
        await env.guard.guard_request(paid, "paid-model-1", row, total_spent_eur=0.0)
    assert exc.value.code == "BUDGET_EXCEEDED"


async def test_paid_allowed_only_when_free_only_off_and_budget_allows(services_env):
    env = services_env
    paid = FakePaidProvider()
    row = await seed_paid_row(env)
    await env.settings_service.set("free_only", False)
    await env.settings_service.set("max_spend", 10.0)
    await env.guard.guard_request(paid, "paid-model-1", row, total_spent_eur=0.0)
    with pytest.raises(BudgetExceeded):
        await env.guard.guard_request(paid, "paid-model-1", row, total_spent_eur=10.0)


async def test_free_only_toggle_is_backend_enforced(services_env):
    """Even with the toggle flipped and flipped back, paid stays blocked."""
    env = services_env
    paid = FakePaidProvider()
    await env.settings_service.set("free_only", True)
    with pytest.raises(PaidModelBlocked):
        await env.guard.guard_request(paid, "paid-model-1", None, total_spent_eur=0.0)
    assert paid.chat_calls == 0  # guard runs BEFORE any provider traffic
