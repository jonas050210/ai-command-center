"""AI Command Center — desktop launcher.

Starts the real FastAPI backend in-process on a free loopback port and
shows it in a native window (pywebview). If pywebview is unavailable,
falls back to the default browser — the app is identical either way.

Run from source:   python desktop/main_desktop.py
Frozen build:      see desktop/aicc_desktop.spec (PyInstaller onedir)
"""
from __future__ import annotations

import os
import socket
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def wait_ready(port: int, timeout_s: float = 30.0) -> None:
    deadline = time.monotonic() + timeout_s
    url = f"http://127.0.0.1:{port}/api/health"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                if resp.status == 200:
                    return
        except OSError:
            time.sleep(0.15)
    raise SystemExit(f"Backend did not become ready on port {port}.")


def serve(port: int) -> None:
    import uvicorn

    from backend.app.config import get_settings
    from backend.app.main import create_app

    settings = get_settings()
    app = create_app(settings)
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


def main() -> None:
    smoke = "--smoke" in sys.argv[1:]
    port = free_port()
    os.environ.setdefault("HOST", "127.0.0.1")
    os.environ["PORT"] = str(port)

    thread = threading.Thread(target=serve, args=(port,), daemon=True)
    thread.start()
    wait_ready(port)
    url = f"http://127.0.0.1:{port}"

    if smoke:
        # CI smoke run: backend up + healthy, then exit deterministically.
        # (windowed frozen builds discard stdout, so also drop a marker file
        # and rely on exit code 0 = healthy / 1 = failed to become ready)
        print(f"SMOKE_OK {url}", flush=True)
        try:
            Path("smoke-ok.txt").write_text(url, encoding="utf-8")
        except OSError:
            pass
        return

    try:
        import webview
    except ImportError:
        webview = None

    if webview is None:
        print(f"AI Command Center running at {url} (browser mode)")
        webbrowser.open(url)
        thread.join()
        return

    webview.create_window("AI Command Center", url, width=1440, height=920,
                          min_size=(1024, 680))
    webview.start()


if __name__ == "__main__":
    main()
