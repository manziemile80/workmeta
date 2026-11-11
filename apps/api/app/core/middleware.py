"""HTTP middleware: request correlation, access logging, security headers,
request-size caps and rate limiting for sensitive unauthenticated endpoints."""

from __future__ import annotations

import re
import threading
import time
import uuid
from collections import deque
from collections.abc import Awaitable, Callable

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.core.config import get_settings

logger = structlog.get_logger(__name__)

_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

# Paths subject to in-memory rate limiting. Everything else only benefits from
# the body-size cap; authenticated per-tenant quotas live in services/quota.py.
_SENSITIVE_LIMITS: tuple[tuple[str, str, str], ...] = (
    ("/api/v1/auth/login", "POST", "rate_limit_login_per_minute"),
    ("/api/v1/invitations/accept", "POST", "rate_limit_invite_accept_per_minute"),
    ("/api/v1/billing/webhooks", "POST", "rate_limit_webhook_per_minute"),
    ("/messages", "POST", "rate_limit_chat_per_minute"),
)

_sensitive_cache_control_prefixes = (
    "/api/v1/auth",
    "/api/v1/organizations/",
    "/api/v1/platform",
    "/api/v1/billing",
)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Sliding-window per-IP limiter for sensitive endpoints (in-memory).

    This is a second layer behind the reverse proxy. It is per-process, so
    multiple uvicorn workers each keep their own buckets — configure the proxy
    as the authoritative limit in production.
    """

    def __init__(self, app) -> None:  # type: ignore[no-untyped-def]
        super().__init__(app)
        self._hits: dict[tuple[str, str], deque[float]] = {}
        self._lock = threading.Lock()

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        settings = get_settings()
        if not settings.rate_limit_enabled:
            return await call_next(request)

        path = request.url.path
        limit = 0
        bucket = ""
        for prefix, method, setting in _SENSITIVE_LIMITS:
            if request.method == method and path.startswith(prefix):
                limit = int(getattr(settings, setting))
                bucket = prefix
                break
        if limit <= 0:
            return await call_next(request)

        client_ip = request.client.host if request.client else "unknown"
        key = (bucket, client_ip)
        now = time.monotonic()
        with self._lock:
            hits = self._hits.setdefault(key, deque())
            while hits and hits[0] <= now - 60.0:
                hits.popleft()
            hits.append(now)
            count = len(hits)
        if count > limit:
            logger.warning(
                "rate_limit_exceeded", path=path, client_ip=client_ip
            )
            return JSONResponse(
                status_code=429,
                content={
                    "error": {
                        "code": "rate_limited",
                        "message": "Too many requests. Please slow down and try again.",
                        "request_id": getattr(request.state, "request_id", ""),
                    }
                },
            )
        return await call_next(request)


class BodyLimitMiddleware(BaseHTTPMiddleware):
    """Reject oversized bodies by Content-Length before parsing.

    Chunked/streamed bodies are not covered here; handlers must also stream
    reads (document upload already reads at most ``max_upload_bytes + 1``).
    """

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        settings = get_settings()
        declared = request.headers.get("content-length")
        if declared:
            try:
                length = int(declared)
            except ValueError:
                length = -1
            if length > settings.max_request_body_bytes:
                logger.warning(
                    "request_body_too_large",
                    path=request.url.path,
                    content_length=length,
                )
                return JSONResponse(
                    status_code=413,
                    content={
                        "error": {
                            "code": "payload_too_large",
                            "message": "The request body is too large.",
                            "request_id": getattr(request.state, "request_id", ""),
                        }
                    },
                )
        return await call_next(request)


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Attach a request ID, bind log context and emit one access log line."""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        supplied = request.headers.get("x-request-id", "")
        request_id = (
            supplied if _REQUEST_ID_PATTERN.match(supplied) else uuid.uuid4().hex
        )
        request.state.request_id = request_id

        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            request_id=request_id,
            method=request.method,
            path=request.url.path,
        )

        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            duration_ms = round((time.perf_counter() - started) * 1000, 1)
            logger.exception("request_failed", duration_ms=duration_ms)
            raise
        finally:
            structlog.contextvars.clear_contextvars()

        duration_ms = round((time.perf_counter() - started) * 1000, 1)
        # Metrics registry (request counts + latency sums). Path is templated
        # to avoid cardinality explosion from UUIDs.
        try:
            from app.api.v1.health import REQUEST_METRICS

            route = request.scope.get("route")
            path_label = (
                getattr(route, "path", None) or request.url.path
            )
            key = (request.method, path_label, str(response.status_code))
            count, latency_sum = REQUEST_METRICS.get(key, (0, 0.0))
            REQUEST_METRICS[key] = (count + 1, latency_sum + duration_ms)
        except Exception as exc:  # noqa: BLE001 - metrics are best-effort
            logger.debug("metrics_collect_failed", error_type=type(exc).__name__)
        response.headers["X-Request-ID"] = request_id
        logger.info(
            "request_completed",
            status_code=response.status_code,
            duration_ms=duration_ms,
            client_ip=request.client.host if request.client else None,
        )
        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Conservative headers suitable for a JSON API."""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        from app.core.config import get_settings

        settings = get_settings()
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault(
            "Permissions-Policy", "camera=(), microphone=(), geolocation=()"
        )
        response.headers.setdefault("Cross-Origin-Resource-Policy", "same-site")
        # HSTS is only meaningful (and only valid) over HTTPS; the reverse
        # proxy terminates TLS and should also emit it.
        if settings.hsts_enabled:
            response.headers.setdefault(
                "Strict-Transport-Security",
                "max-age=31536000; includeSubDomains",
            )
        # The JSON API renders no HTML, so lock down any HTML context (docs).
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'none'; frame-ancestors 'none'",
        )
        path = request.url.path
        if path.startswith(_sensitive_cache_control_prefixes):
            response.headers.setdefault("Cache-Control", "no-store")
        return response
