"""Desktop build driver (P9) — frontend → PyInstaller onedir.

Usage (Windows, from repo root):
    python desktop/build.py

Steps (idempotent):
  1. npm ci + production build of the frontend (skipped if dist is fresh)
  2. PyInstaller onedir via desktop/aicc_desktop.spec
Result:
    dist-desktop/AICommandCenter/AICommandCenter.exe  (+ dlls)
The Inno Setup step (desktop/installer.iss) runs separately in CI.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(cmd: list[str], cwd: Path | None = None) -> None:
    print("+", " ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=cwd or ROOT, check=True)


def frontend_fresh() -> bool:
    dist_index = ROOT / "frontend" / "dist" / "index.html"
    if not dist_index.exists():
        return False
    newest_src = max(
        (p.stat().st_mtime for p in (ROOT / "frontend" / "src").rglob("*.*")),
        default=0.0)
    return dist_index.stat().st_mtime >= newest_src


def main() -> None:
    if not (ROOT / "frontend" / "dist" / "index.html").exists() \
            or not frontend_fresh():
        npm = "npm.cmd" if sys.platform == "win32" else "npm"
        run([npm, "ci", "--no-audit", "--no-fund"], cwd=ROOT / "frontend")
        run([npm, "run", "build"], cwd=ROOT / "frontend")
    else:
        print("frontend dist is fresh — skipping rebuild")

    for d in ("dist-desktop", "build-desktop"):
        shutil.rmtree(ROOT / d, ignore_errors=True)
    run([sys.executable, "-m", "PyInstaller",
         str(ROOT / "desktop" / "aicc_desktop.spec"),
         "--distpath", str(ROOT / "dist-desktop"),
         "--workpath", str(ROOT / "build-desktop"),
         "--noconfirm", "--clean"])
    exe = (ROOT / "dist-desktop" / "AICommandCenter"
           / ("AICommandCenter.exe" if sys.platform == "win32"
              else "AICommandCenter"))
    print(f"\nBuilt: {exe}")


if __name__ == "__main__":
    main()
