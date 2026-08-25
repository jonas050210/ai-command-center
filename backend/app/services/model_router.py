"""Model router — resolves (provider, model) for each request.

Routing policy (Phase 1-3): an explicit provider in the request wins;
otherwise the model is looked up in the synced ``models`` table;
otherwise it falls back to the default (only) registered provider —
Ollama. There is intentionally **no fallback across providers**: a
failure is surfaced, never silently rerouted to a paid runtime.
"""
from __future__ import annotations

import re

from ..db.repo import ModelsRepo
from ..providers.base import Provider
from ..providers.registry import ProviderRegistry
from .settings_service import SettingsService

ALL_CATEGORIES = ["general", "reasoning", "coding", "research", "vision",
                  "creative", "fast", "local", "free", "experimental"]

_PARAM_RE = re.compile(r"(\d+(?:\.\d+)?)\s*([bBmM])")


def parse_params_billions(parameter_size: str | None) -> float | None:
    """'0.6B' → 0.6, '7B' → 7.0, '1.5B' → 1.5. None if undeterminable."""
    if not parameter_size:
        return None
    m = _PARAM_RE.search(parameter_size.replace(",", ""))
    if not m:
        return None
    value = float(m.group(1))
    return value if m.group(2).lower() == "b" else value / 1000.0


def classify_model(name: str, families: list[str], capabilities: list[str],
                   parameter_size: str | None) -> list[str]:
    """Assign UI categories from real metadata (heuristic and honest —
    only derived from the model's actual name/families/capabilities)."""
    lname = name.lower()
    fams = {f.lower() for f in families}
    caps = {c.lower() for c in capabilities}
    cats = {"general", "local", "free"}

    if ("vision" in caps or "clip" in fams or "mllama" in fams
            or any(t in lname for t in ("llava", "vision", "-vl", "minicpm-v", "moondream"))):
        cats.add("vision")
    if any(t in lname for t in ("coder", "code", "codestral", "starcoder", "codegemma")):
        cats.add("coding")
    if any(t in lname for t in ("r1", "qwq", "reason", "think", "o1", "o3")):
        cats.add("reasoning")
    if any(t in lname for t in ("research", "scholar")):
        cats.add("research")
    if any(t in lname for t in ("creative", "story", "mytho", "novel", "writer")):
        cats.add("creative")
    if any(t in lname for t in ("dev", "alpha", "beta", "experimental", "test")):
        cats.add("experimental")
    size_b = parse_params_billions(parameter_size)
    if size_b is not None and size_b <= 2.0:
        cats.add("fast")

    return [c for c in ALL_CATEGORIES if c in cats]


class ModelRouter:
    def __init__(self, registry: ProviderRegistry, models: ModelsRepo,
                 settings: SettingsService):
        self.registry = registry
        self.models = models
        self.settings = settings

    async def resolve(self, provider_name: str | None,
                      model_name: str | None) -> tuple[Provider, str, dict | None]:
        if not provider_name:
            provider_name = self.registry.names()[0] if len(self.registry.names()) == 1 else "ollama"
        provider = self.registry.get(provider_name)
        if not model_name:
            model_name = await self.settings.get_typed("default_model")
        row = await self.models.get(provider_name, model_name)
        return provider, model_name, row
