"""Configuration system.

Single source of truth for environment-driven configuration.
Nothing (including the default model name) is hardcoded anywhere else
in the application — every module reads from ``Settings``.

Precedence: real environment variables > ``.env`` file > defaults below.
"""
from __future__ import annotations

import os
import sys
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# App version is defined EXACTLY ONCE (here).
APP_VERSION = "0.12.0"


# ── desktop / frozen app (PyInstaller onedir, P9) ────────────────────
def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def bundle_root() -> Path:
    """Read-only resources root: PyInstaller's _MEIPASS, else repo root."""
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", PROJECT_ROOT))
    return PROJECT_ROOT


def default_data_dir() -> Path:
    """Data-dir priority: explicit DATA_DIR env (pydantic-settings already
    handles that) → frozen: exe-adjacent ``data/`` when writable (portable
    install) else ``%LOCALAPPDATA%/AICommandCenter`` → dev: repo ``data/``.
    """
    if not is_frozen():
        return PROJECT_ROOT / "data"
    exe_dir = Path(sys.executable).resolve().parent
    candidate = exe_dir / "data"
    try:
        candidate.mkdir(parents=True, exist_ok=True)
        probe = candidate / ".write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return candidate
    except OSError:
        base = os.environ.get("LOCALAPPDATA")
        root = (Path(base) if base
                else Path.home() / "AppData" / "Local") / "AICommandCenter"
        return root


def _env_file() -> Path:
    if is_frozen():
        return Path(sys.executable).resolve().parent / ".env"
    return PROJECT_ROOT / ".env"

# The default model is defined EXACTLY ONCE (here). The rest of the app
# always reads settings.default_model or the runtime settings table.
DEFAULT_MODEL_NAME = "qwen3:0.6b"


class Settings(BaseSettings):
    """Environment configuration (env vars are case-insensitive)."""

    model_config = SettingsConfigDict(
        env_file=str(_env_file()),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "AI Command Center"
    version: str = APP_VERSION

    # server
    host: str = "127.0.0.1"
    port: int = 8000

    # storage — see default_data_dir() for frozen-app resolution
    data_dir: Path = Field(default_factory=default_data_dir)
    log_level: str = "INFO"

    # ollama
    ollama_host: str = "http://localhost:11434"
    default_model: str = DEFAULT_MODEL_NAME
    ollama_num_ctx: int = 8192
    ollama_keep_alive: str = "10m"
    ollama_timeout: float = 300.0

    # openrouter (cloud gateway — OpenAI-compatible). Change only for
    # self-hosted proxies/testing; the key comes from the vault, not env.
    openrouter_base_url: str = "https://openrouter.ai/api"

    # ── strict €0 cost protection (HARD REQUIREMENT, on by default) ──
    free_only: bool = True            # FREE_ONLY
    max_spend: float = 0.0            # MAX_SPEND (EUR, lifetime budget)
    currency: str = "EUR"

    # security
    ai_cc_secret_key: str | None = None
    workspace_root: Path | None = None

    # ── API hardening (Phase 4 / P0) ─────────────────────────────────
    # Explicit API token. If unset, one is generated on demand and stored
    # in <DATA_DIR>/api.token with restricted permissions. Only *required*
    # when binding off-loopback; loopback operation needs no token.
    ai_cc_api_token: str | None = None
    # Extra Host header names to accept (e.g. a LAN DNS name when binding
    # 0.0.0.0). Loopback names are always accepted.
    extra_allowed_hosts: str = ""
    # Built-in sliding-window rate limits on sensitive endpoints
    enable_rate_limits: bool = True

    # default provider used when a request doesn't name one
    default_provider: str = "ollama"

    # EUR per 1 USD — cloud providers (OpenRouter) price in USD; the
    # catalog stores EUR so CostGuard can enforce a single-currency budget.
    eur_per_usd: float = 0.92

    # log rotation (JSON file handler)
    log_max_bytes: int = 5_000_000
    log_backups: int = 3

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
        return bundle_root() / "frontend" / "dist"

    @property
    def resolved_workspace_root(self) -> Path:
        return (self.workspace_root or (self.data_dir / "workspace")).resolve()

    @field_validator("data_dir", mode="before")
    @classmethod
    def _expand(cls, v):  # noqa: N805
        return Path(os.path.expanduser(str(v))) if v is not None else v

    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def api_token_path(self) -> Path:
        return self.data_dir / "api.token"

    @property
    def binds_loopback(self) -> bool:
        h = self.host.strip().lower()
        return h in {"127.0.0.1", "localhost", "::1", "[::1]"} or h.startswith("127.")

    def allowed_hostnames(self) -> set[str]:
        """Host-header names the server will answer to (DNS-rebinding guard)."""
        hosts = {"127.0.0.1", "localhost", "::1"}
        if not self.binds_loopback:
            hosts.add(self.host.strip().lower())
        for extra in self.extra_allowed_hosts.split(","):
            extra = extra.strip().lower()
            if extra:
                hosts.add(extra)
        return hosts

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.resolved_workspace_root.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.ensure_dirs()
    return s
