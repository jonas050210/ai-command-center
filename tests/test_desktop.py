"""Desktop packaging (P9) — frozen-mode path logic + build plumbing.

No actual PyInstaller run here (needs a full bundle build); everything
that differs in a frozen app (data dir, .env, bundle root, port picking)
is verified directly.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from backend.app import config
from backend.app.config import Settings
from desktop.main_desktop import free_port


class TestFrozenPaths:
    def test_dev_mode_defaults(self):
        assert config.is_frozen() is False
        assert config.bundle_root() == config.PROJECT_ROOT
        assert config.default_data_dir() == config.PROJECT_ROOT / "data"

    def test_frozen_exe_adjacent_when_writable(self, monkeypatch, tmp_path):
        exe = tmp_path / "AICommandCenter.exe"
        exe.write_text("x")
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "executable", str(exe))
        monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "bundle"),
                            raising=False)
        assert config.is_frozen() is True
        # exe-adjacent data dir wins (created + probed)
        assert config.default_data_dir() == tmp_path / "data"
        assert (tmp_path / "data").is_dir()
        # probe file is cleaned up
        assert not (tmp_path / "data" / ".write_probe").exists()
        assert config.bundle_root() == tmp_path / "bundle"
        assert config._env_file() == tmp_path / ".env"

    def test_frozen_falls_back_to_localappdata(self, monkeypatch, tmp_path):
        exe = tmp_path / "app.exe"
        exe.write_text("x")
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "executable", str(exe))
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "lad"))

        # make exe dir creation fail → fallback path
        real_mkdir = Path.mkdir

        def fake_mkdir(self, *a, **kw):
            if self == tmp_path / "data":
                raise OSError("read-only")
            return real_mkdir(self, *a, **kw)

        monkeypatch.setattr(Path, "mkdir", fake_mkdir)
        out = config.default_data_dir()
        assert out == tmp_path / "lad" / "AICommandCenter"

    def test_explicit_data_dir_env_still_wins(self, monkeypatch, tmp_path):
        exe = tmp_path / "app.exe"
        exe.write_text("x")
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "executable", str(exe))
        monkeypatch.setenv("DATA_DIR", str(tmp_path / "chosen"))
        s = Settings(_env_file=None)
        assert s.data_dir == tmp_path / "chosen"

    def test_frontend_dist_under_bundle(self, monkeypatch, tmp_path):
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "bundle"),
                            raising=False)
        s = Settings(data_dir=tmp_path / "d", _env_file=None)
        assert s.frontend_dist == tmp_path / "bundle" / "frontend" / "dist"


class TestDesktopLauncher:
    def test_free_port_is_bindable(self):
        import socket
        port = free_port()
        assert 1024 < port < 65536
        with socket.socket() as s:
            s.bind(("127.0.0.1", port))     # really free


class TestPackagingFiles:
    ROOT = Path(__file__).resolve().parents[1]

    def test_spec_parses_and_references_launcher(self):
        src = (self.ROOT / "desktop" / "aicc_desktop.spec").read_text()
        assert "main_desktop.py" in src
        assert "frontend/dist" in src
        assert 'name="AICommandCenter"' in src
        compile(src, "aicc_desktop.spec", "exec")   # valid Python

    def test_installer_script_references_version(self):
        src = (self.ROOT / "desktop" / "installer.iss").read_text()
        assert "AppVersion" in src and "AICommandCenter" in src

    def test_release_workflow_builds_and_tests(self):
        src = (self.ROOT / ".github" / "workflows" / "release.yml").read_text()
        assert "windows-latest" in src
        assert "pytest -q" in src               # never ship red
        assert "desktop/build.py" in src
        assert "ISCC.exe" in src
        assert 'tags: ["v*"]' in src

    def test_desktop_requirements_separate(self):
        main_reqs = (self.ROOT / "requirements.txt").read_text()
        assert "pyinstaller" not in main_reqs   # runtime stays lean
        desk = (self.ROOT / "requirements-desktop.txt").read_text()
        assert "pyinstaller" in desk and "pywebview" in desk

    def test_build_script_has_frontend_step(self):
        src = (self.ROOT / "desktop" / "build.py").read_text()
        assert "npm" in src and "aicc_desktop.spec" in src
