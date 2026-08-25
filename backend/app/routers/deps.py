"""Dependency accessors for app.state services (keeps routers thin)."""
from __future__ import annotations

from fastapi import Request


def services(request: Request):
    return request.app.state.services
