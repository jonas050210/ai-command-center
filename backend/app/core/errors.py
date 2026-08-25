"""Central error taxonomy + FastAPI exception handlers.

Every API error shares one JSON shape::

    {"error": {"code": "...", "message": "...", "details": {...}}}
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

log = logging.getLogger("aicc.errors")


class AppError(Exception):
    """Base application error carrying an HTTP status and stable code."""

    status_code = 500
    code = "INTERNAL_ERROR"

    def __init__(self, message: str, *, details: dict[str, Any] | None = None,
                 status_code: int | None = None, code: str | None = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}
        if status_code is not None:
            self.status_code = status_code
        if code is not None:
            self.code = code


class PaidModelBlocked(AppError):
    """Raised by CostGuard BEFORE any provider network request."""

    status_code = 403
    code = "PAID_MODEL_BLOCKED"


class BudgetExceeded(AppError):
    status_code = 403
    code = "BUDGET_EXCEEDED"


class ProviderUnavailable(AppError):
    status_code = 503
    code = "PROVIDER_UNAVAILABLE"


class ProviderError(AppError):
    status_code = 502
    code = "PROVIDER_ERROR"


class NotFound(AppError):
    status_code = 404
    code = "NOT_FOUND"


class BadRequest(AppError):
    status_code = 400
    code = "BAD_REQUEST"


class PathEscapeError(AppError):
    status_code = 403
    code = "PATH_ESCAPE_BLOCKED"


class FeatureNotImplemented(AppError):
    """Future-phase feature — must never pretend to work."""

    status_code = 501
    code = "NOT_IMPLEMENTED"


def error_payload(code: str, message: str, details: dict[str, Any] | None = None) -> dict:
    return {"error": {"code": code, "message": message, "details": details or {}}}


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        if exc.status_code >= 500:
            log.error("app_error code=%s msg=%s", exc.code, exc.message)
        else:
            log.info("app_error code=%s msg=%s", exc.code, exc.message)
        return JSONResponse(
            status_code=exc.status_code,
            content=error_payload(exc.code, exc.message, exc.details),
        )

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled_exception: %s", exc)
        return JSONResponse(
            status_code=500,
            content=error_payload("INTERNAL_ERROR", "An unexpected internal error occurred."),
        )
