"""First-run setup (idempotent, Windows/Linux/macOS).

    python setup.py                → venv + backend deps + frontend build + .env
    python setup.py --no-venv      → install into the current Python
    python setup.py --skip-frontend
    python setup.py --pull-model   → also `ollama pull <DEFAULT_MODEL>` when present

This is a project bootstrap script, not a packaging file.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEFAULT_MODEL = os.environ.get("DEFAULT_MODEL", "qwen3:0.6b")


def run(cmd: list[str], **kw) -> int:
    print(f"[setup] $ {' '.join(cmd)}")
    return subprocess.call(cmd, cwd=str(ROOT), **kw)


def npm_cmd() -> str | None:
    return shutil.which("npm") or shutil.which("npm.cmd")


def main() -> int:
    p = argparse.ArgumentParser(description="AI Command Center setup")
    p.add_argument("--no-venv", action="store_true")
    p.add_argument("--skip-frontend", action="store_true")
    p.add_argument("--pull-model", action="store_true")
    args = p.parse_args()

    if sys.version_info < (3, 11):
        print(f"[setup] Python 3.11+ required (found {sys.version.split()[0]}).")
        return 1

    # 1. virtual environment -------------------------------------------------
    python = sys.executable
    if not args.no_venv:
        venv = ROOT / ".venv"
        if not venv.exists():
            if run([sys.executable, "-m", "venv", str(venv)]) != 0:
                print("[setup] venv creation failed"); return 1
        for candidate in (venv / "Scripts" / "python.exe", venv / "bin" / "python"):
            if candidate.exists():
                python = str(candidate)
    print(f"[setup] python: {python}")

    # 2. backend dependencies -------------------------------------------------
    if run([python, "-m", "pip", "install", "--upgrade", "pip"]) != 0:
        return 1
    if run([python, "-m", "pip", "install", "-r", "requirements.txt"]) != 0:
        return 1

    # 3. .env -----------------------------------------------------------------
    env_file, env_example = ROOT / ".env", ROOT / ".env.example"
    if not env_file.exists() and env_example.exists():
        shutil.copy(env_example, env_file)
        print("[setup] created .env from .env.example")

    # 4. frontend --------------------------------------------------------------
    if not args.skip_frontend:
        npm = npm_cmd()
        if npm is None:
            print("[setup] npm not found — install Node.js 22 LTS, then rerun setup.")
            return 1
        if run([npm, "install"], cwd=str(ROOT / "frontend")) != 0:
            return 1
        if run([npm, "run", "build"], cwd=str(ROOT / "frontend")) != 0:
            return 1

    # 5. ollama -----------------------------------------------------------------
    ollama = shutil.which("ollama")
    if ollama is None:
        print("[setup] Ollama not found on PATH. Install it to enable local AI:")
        print("         Windows: winget install Ollama.Ollama   (or ollama.com/download)")
        print(f"         Then:    ollama pull {DEFAULT_MODEL}")
    else:
        print(f"[setup] Ollama found: {ollama}")
        if args.pull_model:
            run([ollama, "pull", DEFAULT_MODEL])

    print("\n[setup] done. Start with:  python start.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
