# PyInstaller spec — AI Command Center (onedir, Windows target; also
# works on Linux/macOS for smoke builds). Build:
#   pyinstaller desktop/aicc_desktop.spec --distpath dist-desktop --workpath build-desktop
from pathlib import Path

ROOT = Path(SPECPATH)                      # repo root (spec lives in desktop/)

a = Analysis(
    [str(ROOT / "desktop" / "main_desktop.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        # the built SPA — served by the FastAPI static mount
        (str(ROOT / "frontend" / "dist"), "frontend/dist"),
    ],
    hiddenimports=[
        "uvicorn.logging", "uvicorn.lifespan.on", "uvicorn.lifespan.off",
        "uvicorn.protocols.http.h11_impl", "uvicorn.protocols.http.auto",
        "uvicorn.protocols.websockets.auto", "uvicorn.protocols.websockets.wsproto_impl",
        "uvicorn.loops.auto", "uvicorn.loops.asyncio",
        "aiosqlite", "cryptography.fernet",
        "ddgs", "trafilatura", "lxml", "lxml_html_clean",
        "webview",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "pytest_asyncio", "pip", "setuptools"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="AICommandCenter",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,                 # windowed app (no console flash)
    disable_windowed_traceback=False,
    icon=None,
)

coll = COLLECT(
    exe, a.binaries, a.datas,
    strip=False, upx=False, upx_exclude=[],
    name="AICommandCenter",
)
