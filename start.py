"""AI Command Center — cross-platform launcher (Windows 11 / Linux / macOS).

    python start.py              → verify environment, install only what is
                                   genuinely missing, then start the app
    python start.py --dev        → autoreload (uvicorn --reload)
    python start.py --open       → also open the browser
    python start.py --check-only → verify only, do not start
    python start.py --port 9000  → custom port

Idempotent by design: dependency checks are import-based (no repeated
downloads or reinstalls once installed), the frontend is built only when
dist/ is missing or older than the sources, and .env is created once.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REQUIREMENTS = ROOT / "requirements.txt"
DEV_FILE = ROOT / ".env"
ENV_EXAMPLE = ROOT / ".env.example"
FRONTEND = ROOT / "frontend"
DIST = FRONTEND / "dist"
DEFAULT_MODEL = "qwen3:0.6b"

# modules that must import for the backend to run
BACKEND_MODULES = ["fastapi", "uvicorn", "pydantic", "pydantic_settings",
                   "httpx", "aiosqlite", "cryptography"]
TEST_MODULES = ["pytest", "pytest_asyncio"]


def log(msg: str) -> None:
    print(f"[start] {msg}")


# ── environment checks ───────────────────────────────────────────────
def venv_python() -> Path | None:
    for candidate in (ROOT / ".venv" / "Scripts" / "python.exe",   # Windows
                      ROOT / ".venv" / "bin" / "python"):          # POSIX
        if candidate.exists():
            return candidate
    return None


def python_version_ok(python: str) -> bool:
    try:
        out = subprocess.check_output([python, "-c",
                                       "import sys;print(sys.version_info[0:2])"],
                                      text=True).strip()
        major, minor = (int(x) for x in out.strip("()").split(", "))
        return (major, minor) >= (3, 11)
    except Exception:
        return False


def deps_present(python: str) -> tuple[bool, list[str]]:
    """Import-based check — cheap and truthful."""
    script = ("import importlib.util,sys;"
              "mods=sys.argv[1:];"
              "missing=[m for m in mods if importlib.util.find_spec(m) is None];"
              "sys.exit(1 if missing else 0)")
    probe = subprocess.run([python, "-c", script, *BACKEND_MODULES],
                           capture_output=True)
    if probe.returncode != 0:
        return False, BACKEND_MODULES
    test = subprocess.run([python, "-c", script, *TEST_MODULES],
                          capture_output=True)
    if test.returncode != 0:
        return False, TEST_MODULES
    return True, []


# ── steps ────────────────────────────────────────────────────────────
def ensure_python() -> str:
    python = sys.executable
    venv = venv_python()
    if venv is not None:
        python = str(venv)
    if not python_version_ok(python):
        log(f"ERROR: Python 3.11+ required (found via {python}). "
            "Install Python 3.11/3.12/3.13 and retry.")
        raise SystemExit(1)
    return python


def ensure_dependencies(python: str, allow_install: bool = True) -> None:
    ok, missing = deps_present(python)
    if ok:
        log("backend dependencies present (verified by import) ✓")
        return
    log(f"missing backend modules: {', '.join(missing)}")
    if not allow_install:
        log("ERROR: dependencies missing and --no-install was requested.")
        raise SystemExit(1)

    if Path(python).resolve().parent.parent.name == ".venv":
        cmd = [python, "-m", "pip", "install", "-r", str(REQUIREMENTS)]
    else:
        # system/global interpreter → create local .venv to avoid touching it
        venv = ROOT / ".venv"
        if not venv.exists():
            log("creating virtual environment (.venv)…")
            if subprocess.call([sys.executable, "-m", "venv", str(venv)]) != 0:
                log("ERROR: venv creation failed.")
                raise SystemExit(1)
        python = str(venv_python())
        if not python_version_ok(python):
            log("ERROR: .venv was created with an incompatible Python.")
            raise SystemExit(1)
        cmd = [python, "-m", "pip", "install", "-r", str(REQUIREMENTS)]

    log("installing backend dependencies (once — future runs skip this)…")
    if subprocess.call(cmd) != 0:
        log("ERROR: dependency installation failed. Check the output above.")
        raise SystemExit(1)
    ok, missing = deps_present(python)
    if not ok:
        log(f"ERROR: still missing after install: {', '.join(missing)}")
        raise SystemExit(1)
    log("backend dependencies installed ✓")
    return


def ensure_env() -> None:
    if not DEV_FILE.exists() and ENV_EXAMPLE.exists():
        shutil.copy(ENV_EXAMPLE, DEV_FILE)
        log(f"created .env from .env.example (model default: {DEFAULT_MODEL})")
    elif not DEV_FILE.exists():
        DEV_FILE.write_text(
            f"DEFAULT_MODEL={DEFAULT_MODEL}\nFREE_ONLY=true\nMAX_SPEND=0.00\n",
            encoding="utf-8")
        log("created minimal .env (defaults: FREE_ONLY=true, MAX_SPEND=0.00)")


def npm_cmd() -> str | None:
    return shutil.which("npm") or shutil.which("npm.cmd")


def ensure_frontend(allow_install: bool = True) -> bool:
    """Build only when missing or stale. Returns True if served UI is ready."""
    npm = npm_cmd()
    if not (DIST / "index.html").exists():
        if npm is None or not allow_install:
            log("frontend not built (npm unavailable or --no-install) — "
                "API runs, UI needs `npm install && npm run build` in frontend/")
            return False
        install = not (FRONTEND / "node_modules").exists()
        if install:
            log("installing frontend dependencies (npm install)…")
            if subprocess.call([npm, "install", "--no-audit", "--no-fund"],
                               cwd=FRONTEND) != 0:
                log("ERROR: npm install failed.")
                return False
        log("building frontend…")
        if subprocess.call([npm, "run", "build"], cwd=FRONTEND) != 0:
            log("ERROR: frontend build failed; API keeps running.")
            return False
        log("frontend built ✓")
        return True

    # stale check: ANY source/config newer than dist/index.html → rebuild
    # (rglob "src/*" does not recurse, so walk the whole tree instead)
    sources = [FRONTEND / "package.json", FRONTEND / "vite.config.ts",
               FRONTEND / "tsconfig.json", FRONTEND / "index.html"]
    for entry in FRONTEND.rglob("*"):
        if not entry.is_file():
            continue
        if entry.is_relative_to(FRONTEND / "node_modules") or \
                entry.is_relative_to(FRONTEND / "dist"):
            continue
        sources.append(entry)
    newest_src = max(sources, key=lambda p: p.stat().st_mtime if p.exists() else 0)
    if newest_src != DIST / "index.html" and npm:
        log("frontend sources changed — rebuilding…")
        if subprocess.call([npm, "run", "build"], cwd=FRONTEND) != 0:
            log("WARNING: frontend rebuild failed; serving previous build.")
            return True
    return True


def check_ollama() -> None:
    """Report PATH presence AND probe the actual HTTP runtime (honest)."""
    import json as _json
    import urllib.error as _urlerr
    import urllib.request as _urlreq

    ollama = shutil.which("ollama") or shutil.which("ollama.cmd")
    if ollama is None:
        log("Ollama binary not found on PATH — install it (https://ollama.com/download)")
        log(f"  Windows:  winget install Ollama.Ollama   then:  ollama pull {DEFAULT_MODEL}")
    base = os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
    try:
        with _urlreq.urlopen(f"{base}/api/version", timeout=2.0) as res:
            data = _json.loads(res.read().decode("utf-8", errors="replace"))
        log(f"Ollama runtime OK @ {base} (version {data.get('version', '?')})")
    except (_urlerr.URLError, OSError, ValueError) as exc:
        log(f"Ollama runtime NOT reachable @ {base} ({exc.__class__.__name__}) — "
            "start `ollama serve` (or ollama app) before chatting.")


def main() -> int:
    parser = argparse.ArgumentParser(description="AI Command Center launcher")
    parser.add_argument("--dev", action="store_true", help="autoreload")
    parser.add_argument("--open", action="store_true", help="open browser")
    parser.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int,
                        default=int(os.environ.get("PORT", "8000")))
    parser.add_argument("--check-only", action="store_true",
                        help="verify environment and exit")
    parser.add_argument("--no-install", action="store_true",
                        help="never install anything; fail if missing")
    args = parser.parse_args()

    print("=" * 64)
    print(" AI COMMAND CENTER — local-first AI workspace")
    print("=" * 64)

    python = ensure_python()
    log(f"python: {python}")
    ensure_dependencies(python, allow_install=not args.no_install)
    ensure_env()
    ui_ready = ensure_frontend(allow_install=not args.no_install)
    check_ollama()

    if args.check_only:
        log("check-only: environment is ready ✓")
        return 0

    host, port = args.host, args.port
    log(f"starting server → http://{host}:{port}"
        + ("  (UI " + ("served ✓" if ui_ready else "NOT built — API only") + ")"))

    if args.open:
        webbrowser.open(f"http://127.0.0.1:{port}")

    env = {**os.environ, "HOST": host, "PORT": str(port)}
    if args.dev:
        cmd = [python, "-m", "uvicorn", "main:app", "--reload",
               "--host", host, "--port", str(port)]
    else:
        cmd = [python, str(ROOT / "main.py")]
    return subprocess.call(cmd, cwd=str(ROOT), env=env)


if __name__ == "__main__":
    raise SystemExit(main())
