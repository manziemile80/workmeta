"""Consistent application error responses.

Clients receive a predictable envelope:

    {"error": {"code": "...", "message": "...", "request_id": "..."}}

Internal details and stack traces are logged, never returned.
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import (
    InterfaceError as DBInterfaceError,
)
from sqlalchemy.exc import (
    OperationalError as DBOperationalError,
)
from sqlalchemy.exc import (
    TimeoutError as DBTimeoutError,
)
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = structlog.get_logger(__name__)

# Connection-level failures are infrastructure outages, not client errors. The
# asyncpg dialect surfaces raw socket errors (ConnectionRefusedError and
# friends are OSError subclasses), so the whole chain is inspected.
_INFRASTRUCTURE_ERRORS: tuple[type[BaseException], ...] = (
    DBOperationalError,
    DBInterfaceError,
    DBTimeoutError,
    OSError,
)


def _asyncpg_outage_errors() -> tuple[type[BaseException], ...]:
    try:
        from asyncpg.exceptions import (  # noqa: PLC0415 - optional at runtime
            InvalidPasswordError,
            PostgresConnectionError,
        )
    except ImportError:  # pragma: no cover - asyncpg is a hard dependency
        return ()
    return (PostgresConnectionError, InvalidPasswordError)


def _exception_chain(exc: BaseException) -> list[BaseException]:
    """Flatten an exception, its causes and any grouped sub-exceptions."""
    seen: list[BaseException] = []
    stack: list[BaseException] = [exc]
    while stack:
        current = stack.pop()
        if current is None or any(current is item for item in seen):
            continue
        seen.append(current)
        nested = getattr(current, "exceptions", None)
        if nested:  # ExceptionGroup / TaskGroup
            stack.extend(nested)
        for link in ("__cause__", "__context__"):
            linked = getattr(current, link, None)
            if isinstance(linked, BaseException):
                stack.append(linked)
    return seen


def _is_infrastructure_failure(exc: BaseException) -> bool:
    """True when the failure is a database/socket outage rather than a bug."""
    outage_types = _INFRASTRUCTURE_ERRORS + _asyncpg_outage_errors()
    return any(
        isinstance(item, outage_types) for item in _exception_chain(exc)
    )


class AppError(Exception):
    """Base class for expected application errors."""

    status_code: int = 400
    code: str = "bad_request"

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        code: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if status_code is not None:
            self.status_code = status_code
        if code is not None:
            self.code = code
        self.details = details or {}


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "")


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        logger.warning(
            "application_error",
            code=exc.code,
            status_code=exc.status_code,
            path=request.url.path,
        )
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "request_id": _request_id(request),
                    **({"details": exc.details} if exc.details else {}),
                }
            },
        )

    @app.exception_handler(RequestValidationError)
    async def _handle_validation(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        fields = [
            {
                "location": ".".join(str(part) for part in error.get("loc", ())),
                "message": error.get("msg", "Invalid value"),
                "type": error.get("type", "value_error"),
            }
            for error in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "validation_error",
                    "message": "Some fields are invalid.",
                    "request_id": _request_id(request),
                    "details": {"fields": fields},
                }
            },
        )

    @app.exception_handler(StarletteHTTPException)
    async def _handle_http_exception(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        """Return the same envelope for framework errors (404, 405, ...).

        FastAPI's default body (``{"detail": ...}``) leaks the raw reason and
        breaks the documented error contract.
        """
        code = {
            400: "bad_request",
            401: "auth_required",
            403: "forbidden",
            404: "not_found",
            405: "method_not_allowed",
            413: "payload_too_large",
            429: "rate_limited",
        }.get(exc.status_code, "http_error")
        message = exc.detail if isinstance(exc.detail, str) else code
        logger.warning(
            "http_error",
            code=code,
            status_code=exc.status_code,
            path=request.url.path,
        )
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": code,
                    "message": message,
                    "request_id": _request_id(request),
                }
            },
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(Exception)
    async def _handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        if _is_infrastructure_failure(exc):
            from app.core.config import get_settings  # noqa: PLC0415 - avoids a cycle

            settings = get_settings()
            url = (settings.database_url or "").strip()
            if not url or "change-me" in url:
                code = "database_not_configured"
                message = "The database connection is not configured on this deployment."
            else:
                code = "database_unavailable"
                message = "The database is not reachable. Please try again shortly."
            logger.exception(
                "infrastructure_unavailable",
                path=request.url.path,
                method=request.method,
                error_type=type(exc).__name__,
                code=code,
            )
            return JSONResponse(
                status_code=503,
                content={
                    "error": {
                        "code": code,
                        "message": message,
                        "request_id": _request_id(request),
                    }
                },
            )
        logger.exception(
            "unhandled_exception",
            path=request.url.path,
            method=request.method,
            error_type=type(exc).__name__,
        )
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "internal_error",
                    "message": "An unexpected error occurred. Please contact support.",
                    "request_id": _request_id(request),
                }
            },
        )
