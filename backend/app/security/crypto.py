"""Encrypted credential storage (Fernet / AES-128-CBC + HMAC).

Key resolution order:
1. ``AI_CC_SECRET_KEY`` environment variable, or
2. ``<data_dir>/secret.key`` — generated once, permissions chmod 600.

Ciphertexts live in the ``credentials`` table; plaintext never touches disk.
"""
from __future__ import annotations

import logging
import os
import stat

from cryptography.fernet import Fernet, InvalidToken

from ..config import Settings
from ..core.errors import AppError

log = logging.getLogger("aicc.security")


class CredentialVault:
    def __init__(self, settings: Settings):
        self._fernet = Fernet(self._load_key(settings))

    @staticmethod
    def _load_key(settings: Settings) -> bytes:
        if settings.ai_cc_secret_key:
            key = settings.ai_cc_secret_key.encode()
            # sanity check — must be urlsafe base64 32-byte key
            Fernet(key)
            return key
        path = settings.secret_key_path
        if path.exists():
            return path.read_bytes().strip()
        key = Fernet.generate_key()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(key)
        try:
            os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)  # 0600
        except OSError:  # Windows ACLs differ — best effort
            pass
        log.info("generated new credential key at %s", path)
        return key

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, ciphertext: str) -> str:
        try:
            return self._fernet.decrypt(ciphertext.encode()).decode()
        except InvalidToken as exc:
            raise AppError("Stored credential could not be decrypted (key changed?)",
                           code="CREDENTIAL_DECRYPT_FAILED", status_code=500) from exc
