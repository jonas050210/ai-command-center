"""Provider registry — where runtimes are registered and looked up.

Phase 1-3 registers Ollama only. Future cloud providers register here
with their real per-token prices and immediately become subject to the
CostGuard (FREE_ONLY blocks them by default).
"""
from __future__ import annotations

from ..core.errors import BadRequest
from .base import Provider


class ProviderRegistry:
    def __init__(self):
        self._providers: dict[str, Provider] = {}

    def register(self, provider: Provider) -> None:
        self._providers[provider.name] = provider

    def get(self, name: str) -> Provider:
        p = self._providers.get(name)
        if p is None:
            raise BadRequest(
                f"Unknown provider '{name}'. Registered providers: "
                f"{', '.join(sorted(self._providers)) or 'none'}.",
                code="PROVIDER_NOT_FOUND")
        return p

    def names(self) -> list[str]:
        return sorted(self._providers)

    def all(self) -> list[Provider]:
        return [self._providers[n] for n in self.names()]
