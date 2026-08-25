"""AI Command Center — application entry point.

Usage:  python main.py
Env:    HOST, PORT (see .env.example)
"""
from __future__ import annotations

import uvicorn

from backend.app.config import get_settings
from backend.app.main import create_app

settings = get_settings()
app = create_app(settings)

if __name__ == "__main__":
    uvicorn.run("main:app", host=settings.host, port=settings.port, log_level="warning")
