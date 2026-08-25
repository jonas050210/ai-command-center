"""Security foundation tests — credential encryption + workspace boundary."""
import os
import stat

import pytest

from backend.app.core.errors import AppError, PathEscapeError
from backend.app.security.crypto import CredentialVault
from backend.app.security.permissions import (Capability, PermissionPolicy)
from backend.app.workspace import resolve_within


def test_vault_roundtrip(test_settings):
    test_settings.ensure_dirs()
    vault = CredentialVault(test_settings)
    secret = "sk-test-super-secret-key-123"
    ciphertext = vault.encrypt(secret)
    assert ciphertext != secret
    assert vault.decrypt(ciphertext) == secret


def test_vault_key_persists_to_disk_with_restricted_perms(test_settings):
    test_settings.ensure_dirs()
    CredentialVault(test_settings)
    path = test_settings.secret_key_path
    assert path.exists()
    if os.name != "nt":  # POSIX: verify 0600
        mode = stat.S_IMODE(path.stat().st_mode)
        assert mode == 0o600
    vault2 = CredentialVault(test_settings)
    ct = vault2.encrypt("abc")
    assert vault2.decrypt(ct) == "abc"


def test_vault_env_key_override(test_settings, monkeypatch):
    from cryptography.fernet import Fernet
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("AI_CC_SECRET_KEY", key)
    s = test_settings.model_copy(update={"ai_cc_secret_key": key})
    vault = CredentialVault(s)
    ct = vault.encrypt("hello")
    assert vault.decrypt(ct) == "hello"


def test_vault_tamper_detected(test_settings):
    test_settings.ensure_dirs()
    vault = CredentialVault(test_settings)
    ct = vault.encrypt("data")[:-4] + "AAAA"
    with pytest.raises(AppError):
        vault.decrypt(ct)


def test_workspace_containment(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    inside = resolve_within(root, "project/main.py")
    assert inside.is_absolute()
    assert root.resolve() in inside.parents


def test_workspace_escape_blocked(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    for evil in ("../outside.txt", "../../etc/passwd", "/etc/passwd",
                 "..\\windows\\system32"):
        with pytest.raises(PathEscapeError):
            resolve_within(root, evil)


def test_permission_policy_denies_by_default():
    policy = PermissionPolicy()
    assert not policy.allows(Capability.FILESYSTEM_WRITE)
    with pytest.raises(AppError) as exc:
        policy.require(Capability.COMMAND_EXECUTE)
    assert exc.value.code == "PERMISSION_DENIED"


def test_blocked_command_detection():
    assert PermissionPolicy.command_is_blocked("rm -rf /")
    assert PermissionPolicy.command_is_blocked("format C:")
    assert not PermissionPolicy.command_is_blocked("python main.py")
