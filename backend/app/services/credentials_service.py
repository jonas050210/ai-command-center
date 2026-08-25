"""Provider credentials service — the ONLY path between plaintext API
keys and the encrypted ``credentials`` table.

Plaintext flow: set_key → vault.encrypt → store. Load flow: ciphertext →
vault.decrypt → provider.configure(...) (in-memory only). Keys are never
written to logs, never returned by any API (only masked state).
"""
from __future__ import annotations

import logging

from ..db.repo import CredentialsRepo
from ..providers.registry import ProviderRegistry
from ..security.crypto import CredentialVault

log = logging.getLogger("aicc.credentials")


def mask_key(key: str) -> str:
    if len(key) <= 10:
        return "***"
    return f"{key[:6]}…{key[-4:]}"


class CredentialsService:
    def __init__(self, repo: CredentialsRepo, vault: CredentialVault,
                 registry: ProviderRegistry):
        self.repo = repo
        self.vault = vault
        self.registry = registry

    async def set_key(self, provider_name: str, api_key: str) -> dict:
        """Encrypt + store, then configure the live provider instance."""
        provider = self.registry.get(provider_name)
        key = api_key.strip()
        await self.repo.upsert(provider_name, self.vault.encrypt(key))
        configure = getattr(provider, "configure", None)
        if configure:
            configure(key)
        log.info("stored API key for provider %s (%s)", provider_name, mask_key(key))
        return {"provider": provider_name, "configured": True, "masked": mask_key(key)}

    async def delete_key(self, provider_name: str) -> dict:
        provider = self.registry.get(provider_name)
        await self.repo.delete(provider_name)
        configure = getattr(provider, "configure", None)
        if configure:
            configure(None)
        log.info("removed API key for provider %s", provider_name)
        return {"provider": provider_name, "configured": False}

    async def load_into_providers(self) -> list[str]:
        """Boot-time: decrypt stored keys and configure providers in-memory."""
        loaded: list[str] = []
        for name in await self.repo.providers_with_keys():
            ciphertext = await self.repo.get_ciphertext(name)
            if not ciphertext:
                continue
            try:
                provider = self.registry.get(name)
            except Exception:
                log.warning("stored credential for unknown provider %s — ignored", name)
                continue
            try:
                key = self.vault.decrypt(ciphertext)
            except Exception as exc:
                log.error("could not decrypt credential for %s: %s", name, exc)
                continue
            configure = getattr(provider, "configure", None)
            if configure:
                configure(key)
                loaded.append(name)
        if loaded:
            log.info("configured provider keys from vault: %s", ", ".join(loaded))
        return loaded

    async def has_key(self, provider_name: str) -> bool:
        return await self.repo.get_ciphertext(provider_name) is not None

    async def masked(self, provider_name: str) -> str | None:
        ciphertext = await self.repo.get_ciphertext(provider_name)
        if not ciphertext:
            return None
        try:
            return mask_key(self.vault.decrypt(ciphertext))
        except Exception:
            return "stored (undecryptable — key changed?)"
