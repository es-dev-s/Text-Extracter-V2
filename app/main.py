"""OCR-V2 FastAPI entrypoint — production-ready Engine API."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.exception_handlers import http_exception_handler, request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import __version__
from .concurrency import start_pool, stop_pool
from .config import get_settings
from .logconfig import setup_logging
from .middleware import MaxBodySizeMiddleware, RateLimitMiddleware, RequestContextMiddleware
from .routers.ocr import router as ocr_router
from .services.ocr import ocr_backend

logger = logging.getLogger("engine")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    app.state.http = httpx.AsyncClient(
        timeout=httpx.Timeout(settings.groq_timeout, connect=8.0),
        limits=httpx.Limits(max_keepalive_connections=10, max_connections=20),
        headers={"User-Agent": f"ocr-v2-engine/{__version__}"},
    )
    groq_on = settings.groq_title_verify and bool(settings.groq_api_keys)
    workers, queue_timeout = start_pool()
    logger.info(
        "engine ready version=%s env=%s groq=%s keys=%s model=%s ocr=%s "
        "extract_workers=%s queue_timeout=%.0fs",
        __version__,
        settings.environment,
        "on" if groq_on else "off",
        len(settings.groq_api_keys) if groq_on else 0,
        settings.groq_model if groq_on else "-",
        ocr_backend(),
        workers,
        queue_timeout,
    )
    try:
        yield
    finally:
        stop_pool()
        await app.state.http.aclose()


def create_app() -> FastAPI:
    settings = get_settings()
    setup_logging(settings.log_level)

    docs = "/docs" if settings.docs_enabled else None
    redoc = "/redoc" if settings.docs_enabled else None
    openapi = "/openapi.json" if settings.docs_enabled else None

    application = FastAPI(
        title="OCR-V2 Engine",
        version=__version__,
        description=(
            "Native PDF text extraction with OCR fallback. "
            "Returns one document title, numbered headers, and full content. "
            "Stable JSON API for the web demo and a future Next.js client."
        ),
        docs_url=docs,
        redoc_url=redoc,
        openapi_url=openapi,
        lifespan=lifespan,
    )

    multipart_overhead = 1024 * 1024
    # Last added runs first. CORS must wrap every JSON error so a future
    # Next.js origin can read 413/429 responses.
    application.add_middleware(GZipMiddleware, minimum_size=1000)
    if settings.rate_limit_per_minute > 0:
        application.add_middleware(
            RateLimitMiddleware,
            per_minute=settings.rate_limit_per_minute,
        )
    application.add_middleware(
        MaxBodySizeMiddleware,
        max_bytes=settings.max_upload_bytes + multipart_overhead,
    )
    application.add_middleware(RequestContextMiddleware)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=["*"] if settings.cors_allow_all else settings.cors_origins,
        allow_credentials=not settings.cors_allow_all,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
    )

    application.include_router(ocr_router, prefix="/v1", tags=["extract"])

    @application.get("/health", tags=["ops"], include_in_schema=False)
    async def health() -> dict:
        return {"ok": True, "version": __version__}

    @application.get("/ready", tags=["ops"], include_in_schema=False)
    async def ready() -> dict:
        http = getattr(application.state, "http", None)
        if http is None or http.is_closed:
            raise HTTPException(status_code=503, detail="Engine is starting")
        return {"ok": True, "version": __version__}

    @application.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        if isinstance(exc, (HTTPException, StarletteHTTPException, RequestValidationError)):
            if isinstance(exc, RequestValidationError):
                return await request_validation_exception_handler(request, exc)
            return await http_exception_handler(request, exc)
        logger.exception("unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error"},
        )

    web_dir = Path(__file__).resolve().parent.parent / "web"
    if web_dir.is_dir():
        application.mount("/", StaticFiles(directory=web_dir, html=True), name="web")
    else:
        logger.warning("web UI directory missing: %s", web_dir)

    return application


app = create_app()
