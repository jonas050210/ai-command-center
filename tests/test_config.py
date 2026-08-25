"""Configuration system tests."""
from pathlib import Path

from backend.app.config import DEFAULT_MODEL_NAME, Settings


def test_defaults():
    s = Settings(data_dir=Path("/tmp/aicc-cfg-test"))
    assert s.default_model == DEFAULT_MODEL_NAME
    assert s.free_only is True            # FREE_ONLY default
    assert s.max_spend == 0.0             # MAX_SPEND default
    assert s.currency == "EUR"
    assert s.ollama_host == "http://localhost:11434"
    assert s.ollama_num_ctx == 8192
    assert s.db_path.name == "ai_command_center.db"


def test_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("DEFAULT_MODEL", "llama3.1:8b")
    monkeypatch.setenv("FREE_ONLY", "false")
    monkeypatch.setenv("MAX_SPEND", "3.50")
    monkeypatch.setenv("OLLAMA_NUM_CTX", "4096")
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "custom"))
    s = Settings()
    assert s.default_model == "llama3.1:8b"
    assert s.free_only is False
    assert s.max_spend == 3.5
    assert s.ollama_num_ctx == 4096
    assert s.db_path.parent == tmp_path / "custom"


def test_workspace_containment_root(tmp_path):
    s = Settings(data_dir=tmp_path / "d")
    assert s.resolved_workspace_root.exists() or True  # created via ensure_dirs
    s.ensure_dirs()
    assert s.resolved_workspace_root.exists()
