"""Configuration system.

Single source of truth for environment-driven configuration.
Nothing (including the default model name) is hardcoded anywhere else
in the application — every module reads from ``Settings``.

Precedence: real environment variables > ``.env`` file > defaults below.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# The default model is defined EXACTLY ONCE (here). The rest of the app
# always reads settings.default_model or the runtime settings table.
DEFAULT_MODEL_NAME = "qwen3:0.6b"


class Settings(BaseSettings):
    """Environment configuration (env vars are case-insensitive)."""

    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "AI Command Center"
    version: str = "0.3.0"

    # server
    host: str = "127.0.0.1"
    port: int = 8000

    # storage
    data_dir: Path = PROJECT_ROOT / "data"
    log_level: str = "INFO"

    # ollama
    ollama_host: str = "http://localhost:11434"
    default_model: str = DEFAULT_MODEL_NAME
    ollama_num_ctx: int = 8192
    ollama_keep_alive: str = "10m"
    ollama_timeout: float = 300.0

    # ── strict €0 cost protection (HARD REQUIREMENT, on by default) ──
    free_only: bool = True            # FREE_ONLY
    max_spend: float = 0.0            # MAX_SPEND (EUR, lifetime budget)
    currency: str = "EUR"

    # security
    ai_cc_secret_key: str | None = None
    workspace_root: Path | None = None

    # frontend (dev CORS only; production serves same-origin)
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # ── derived paths ────────────────────────────────────────────────
    @property
    def db_path(self) -> Path:
        return self.data_dir / "ai_command_center.db"

    @property
    def log_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def secret_key_path(self) -> Path:
        return self.data_dir / "secret.key"

    @property
    def frontend_dist(self) -> Path:
        return PROJECT_ROOT / "frontend" / "dist"

    @property
    def resolved_workspace_root(self) -> Path:
        return (self.workspace_root or (self.data_dir / "workspace")).resolve()

    @field_validator("data_dir", mode="before")
    @classmethod
    def _expand(cls, v):  # noqa: N805
        return Path(os.path.expanduser(str(v))) if v is not None else v

    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.resolved_workspace_root.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.ensure_dirs()
    return s
