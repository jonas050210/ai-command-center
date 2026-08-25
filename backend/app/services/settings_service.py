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
        else:
            raw = str(value)
        await self.repo.set(key, raw)

    async def as_dict(self) -> dict:
        return {k: await self.get_typed(k) for k in self._defaults}
