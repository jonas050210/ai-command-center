"""Cross-platform launcher (Windows/Linux/macOS).

    python start.py             → start server (uses .venv if present)
    python start.py --dev       → autoreload for development
    python start.py --open      → also open the browser

Prefers the virtual environment created by setup.py; falls back to the
current interpreter. Frontend: serves frontend/dist if built, else the
API still runs and tells you how to build it.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def venv_python() -> Path | None:
    for candidate in (ROOT / ".venv" / "Scripts" / "python.exe",   # Windows
                      ROOT / ".venv" / "bin" / "python"):          # POSIX
        if candidate.exists():
            return candidate
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="AI Command Center launcher")
    parser.add_argument("--dev", action="store_true", help="enable autoreload")
    parser.add_argument("--open", action="store_true", help="open browser on start")
    parser.add_argument("--host", default=os.environ.get("HOST"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    args = parser.parse_args()

    if not (ROOT / "frontend" / "dist" / "index.html").exists():
        print("[start] frontend/dist not found — API will run, UI will prompt to run "
              "'python setup.py'.")
    if not (ROOT / ".env").exists():
        print("[start] no .env found — using built-in defaults (see .env.example).")

    host = args.host or os.environ.get("HOST", "127.0.0.1")
    target = venv_python()
    if target and Path(sys.executable).resolve() != target.resolve():
        cmd = [str(target), str(ROOT / "main.py")]
        if args.dev:
            cmd = [str(target), "-m", "uvicorn", "main:app", "--reload",
                   "--host", host, "--port", str(args.port)]
        env = {**os.environ, "HOST": host, "PORT": str(args.port)}
    else:
        env = {**os.environ, "HOST": host, "PORT": str(args.port)}
        if args.dev:
            cmd = [sys.executable, "-m", "uvicorn", "main:app", "--reload",
                   "--host", host, "--port", str(args.port)]
        else:
            cmd = [sys.executable, str(ROOT / "main.py")]

    print(f"[start] AI Command Center → http://{host}:{args.port}")
    if args.open:
        webbrowser.open(f"http://127.0.0.1:{args.port}")
    return subprocess.call(cmd, cwd=str(ROOT), env=env)


if __name__ == "__main__":
    raise SystemExit(main())
