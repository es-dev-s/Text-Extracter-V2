"""Production middleware: request IDs, upload caps, per-IP rate limits."""

from __future__ import annotations

import logging
import time
import uuid
from collections import defaultdict, deque

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

logger = logging.getLogger("engine.http")

_EXTRACT_PATHS = {"/v1/ocr", "/v1/extract"}


def client_ip(request: Request) -> str:
    if request.client and request.client.host:
        return request.client.host
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return "unknown"


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
        request.state.request_id = request_id
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            logger.exception(
                "unhandled %s %s ip=%s id=%s",
                request.method,
                request.url.path,
                client_ip(request),
                request_id,
            )
            raise
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        if request.url.path not in {"/health", "/ready"}:
            logger.info(
                "%s %s %s %sms ip=%s id=%s",
                request.method,
                request.url.path,
                response.status_code,
                elapsed_ms,
                client_ip(request),
                request_id,
            )
        return response


class MaxBodySizeMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        super().__init__(app)
        self.max_bytes = max_bytes

    async def dispatch(self, request: Request, call_next) -> Response:
        if request.method in {"POST", "PUT", "PATCH"}:
            raw = request.headers.get("content-length")
            if raw:
                try:
                    length = int(raw)
                except ValueError:
                    return JSONResponse(
                        status_code=400,
                        content={"detail": "Invalid Content-Length"},
                    )
                if length > self.max_bytes:
                    return JSONResponse(
                        status_code=413,
                        content={"detail": "Request body is too large"},
                    )
        return await call_next(request)


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp, per_minute: int) -> None:
        super().__init__(app)
        self.per_minute = max(1, per_minute)
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._last_prune = time.monotonic()

    def _allow(self, key: str) -> bool:
        now = time.monotonic()
        window = 60.0
        hits = self._hits[key]
        while hits and now - hits[0] > window:
            hits.popleft()
        if now - self._last_prune > 120:
            stale = [ip for ip, q in self._hits.items() if not q or now - q[-1] > window]
            for ip in stale:
                self._hits.pop(ip, None)
            self._last_prune = now
        if len(hits) >= self.per_minute:
            return False
        hits.append(now)
        return True

    async def dispatch(self, request: Request, call_next) -> Response:
        if request.method == "POST" and request.url.path in _EXTRACT_PATHS:
            if not self._allow(client_ip(request)):
                return JSONResponse(
                    status_code=429,
                    content={"detail": "Too many requests. Try again in a minute."},
                    headers={"Retry-After": "60"},
                )
        return await call_next(request)
