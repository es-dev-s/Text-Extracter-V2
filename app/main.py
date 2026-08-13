"""OCR-V2 FastAPI entrypoint."""

from __future__ import annotations

from contextlib import asynccontextmanager

from pathlib import Path

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from . import __version__
from . import config  # noqa: F401  loads .env
from .routers.ocr import router as ocr_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.http = httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=8.0))
    try:
        yield
    finally:
        await app.state.http.aclose()


app = FastAPI(
    title="OCR-V2 Engine",
    version=__version__,
    description=(
        "Native PDF text extraction with OCR fallback. "
        "Returns document title, numbered headers, and full content."
    ),
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(ocr_router, prefix="/v1", tags=["extract"])


@app.get("/health")
async def health() -> dict:
    return {"ok": True, "version": __version__}


_WEB_DIR = Path(__file__).resolve().parent.parent / "web"
app.mount("/", StaticFiles(directory=_WEB_DIR, html=True), name="web")
