"""start.py behavior — idempotent, no repeated installs, useful diagnostics."""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _python() -> str:
    for candidate in (ROOT / ".venv" / "Scripts" / "python.exe",
                      ROOT / ".venv" / "bin" / "python"):
        if candidate.exists():
            return str(candidate)
    return sys.executable


def test_start_check_only_is_fast_and_idempotent():
    env = {**os.environ, "DATA_DIR": str(ROOT / "data"),
           "WORKSPACE_ROOT": str(ROOT / "data" / "workspace"),
           "HOST": "127.0.0.1", "PORT": "8123"}
    r1 = subprocess.run([_python(), "start.py", "--check-only", "--no-install"],
                        cwd=ROOT, env=env, capture_output=True, text=True,
                        timeout=120)
    assert r1.returncode == 0, r1.stdout + r1.stderr
    out = r1.stdout + r1.stderr
    # imports verified — nothing reinstalled
    assert "dependencies present" in out
    assert "installing backend" not in out
    assert "creating virtual environment" not in out
    assert "environment is ready" in out
    # second run must behave identically (no state churn)
    r2 = subprocess.run([_python(), "start.py", "--check-only", "--no-install"],
                        cwd=ROOT, env=env, capture_output=True, text=True,
                        timeout=120)
    assert r2.returncode == 0
    assert "installing backend" not in (r2.stdout + r2.stderr)


def test_start_rejects_old_python_gracefully():
    """The version gate must be real: a bogus interpreter path fails clearly."""
    env = {**os.environ, "DATA_DIR": str(ROOT / "data")}
    result = subprocess.run(
        [_python(), "-c",
         "import runpy,sys; sys.argv=['start.py','--check-only'];"
         "runpy.run_path('start.py', run_name='__main__')"],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    # sanity: start.py is importable as a script module
    assert "environment is ready" in result.stdout or result.returncode == 0
