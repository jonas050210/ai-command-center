"""Runtime settings — DB-persisted values layered over env defaults.

Env (.env / variables) provides the *initial* value; changes made via
the Settings API persist in the ``settings`` table. This keeps the
frontend able to toggle FREE_ONLY while the backend remains the only
enforcement point.
"""
from __future__ import annotations

from ..config import Settings
from ..core.errors import BadRequest
from ..db.repo import SettingsRepo

_DEFAULTS: dict[str, str] = {}
_TYPES: dict[str, type] = {
    "free_only": bool,
    "max_spend": float,
    "default_model": str,
    "num_ctx": int,
    "keep_alive": str,
    "custom_instructions": str,
    "agent_max_steps": int,
    "agent_max_fix_rounds": int,
    "agent_cmd_timeout": float,
    "team_max_rounds": int,
    "search_engine": str,
    "research_max_sources": int,
}


class SettingsService:
    def __init__(self, repo: SettingsRepo, env: Settings):
        self.repo = repo
        self.env = env
        self._defaults = {
            "free_only": "true" if env.free_only else "false",
            "max_spend": str(env.max_spend),
            "default_model": env.default_model,
            "num_ctx": str(env.ollama_num_ctx),
            "keep_alive": env.ollama_keep_alive,
            "custom_instructions": "",
            "agent_max_steps": str(env.agent_max_steps),
            "agent_max_fix_rounds": str(env.agent_max_fix_rounds),
            "agent_cmd_timeout": str(env.agent_cmd_timeout),
            "team_max_rounds": str(env.team_max_rounds),
            "search_engine": env.search_engine,
            "research_max_sources": str(env.research_max_sources),
        }

    async def get(self, key: str) -> str:
        if key not in self._defaults:
            raise BadRequest(f"Unknown setting '{key}'", code="UNKNOWN_SETTING")
        value = await self.repo.get(key)
        return value if value is not None else self._defaults[key]

    async def get_typed(self, key: str):
        raw = await self.get(key)
        t = _TYPES[key]
        if t is bool:
            return raw.strip().lower() in ("1", "true", "yes", "on")
        if t is float:
            try:
                return float(raw)
            except ValueError:
                return float(self._defaults[key])
        if t is int:
            try:
                return int(raw)
            except ValueError:
                return int(self._defaults[key])
        return raw

    async def set(self, key: str, value) -> None:
        if key not in self._defaults:
            raise BadRequest(f"Unknown setting '{key}'", code="UNKNOWN_SETTING")
        t = _TYPES[key]
        if t is bool:
            if isinstance(value, str):
                value = value.strip().lower() in ("1", "true", "yes", "on")
            raw = "true" if value else "false"
        elif t is float:
            raw = str(float(value))
            if key == "max_spend" and float(raw) < 0:
                raise BadRequest("max_spend cannot be negative")
        elif t is int:
            raw = str(int(value))
            if key == "num_ctx" and int(raw) < 512:
                raise BadRequest("num_ctx must be at least 512 tokens")
            if key in ("agent_max_steps", "agent_max_fix_rounds", "team_max_rounds") \
                    and int(raw) < 0:
                raise BadRequest(f"{key} cannot be negative")
            if key == "agent_max_steps" and int(raw) < 3:
                raise BadRequest("agent_max_steps must be at least 3")
            if key == "research_max_sources" and not 1 <= int(raw) <= 8:
                raise BadRequest("research_max_sources must be between 1 and 8")
        else:
            raw = str(value)
            if key == "search_engine" and raw.strip().lower() not in \
                    ("duckduckgo", "disabled", "none"):
                raise BadRequest("search_engine must be 'duckduckgo' or 'disabled'")
            if key == "search_engine" and raw.strip().lower() == "none":
                raw = "disabled"
        await self.repo.set(key, raw)

    async def as_dict(self) -> dict:
        return {k: await self.get_typed(k) for k in self._defaults}
