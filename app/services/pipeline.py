"""Try native PDF extraction first; OCR only when the page has no readable text."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Optional

import httpx

from ..concurrency import run_extraction
from ..config import get_settings
from ..schemas import ExtractResponse, HeaderItem, PageText
from .groq_title import (
    freeze_title,
    opening_excerpt,
    pick_pdf_title,
    pick_title_from_page_image,
)
from .headings import TitleBand, headers_listed, list_headers, resolve_document_title
from .native import EncryptedPdfError, NativeDocument, NativePdfError, extract_pdf
from .ocr import (
    NO_OCR,
    excerpt_is_usable,
    native_text_is_readable,
    needs_ocr_title,
    recover_title,
    render_title_jpeg,
    title_is_usable,
)

logger = logging.getLogger("engine.title")


def _ms(started: float) -> int:
    return max(0, int((time.perf_counter() - started) * 1000))


def _clip(text: str, limit: int = 96) -> str:
    t = " ".join((text or "").split())
    if len(t) > limit:
        return t[: limit - 1] + "…"
    return t


def _log_step(filename: str, step: str, ms: Optional[int] = None, *, skipped: str = "", **extra: object) -> None:
    """Stdout-only timing. Never attached to the API or UI payload."""
    name = filename or "document.pdf"
    parts = [f"file={name}", f"step={step}"]
    if skipped:
        parts.append("skipped=true")
        parts.append(f"reason={skipped}")
    else:
        parts.append(f"ms={0 if ms is None else ms}")
    for key, value in extra.items():
        if value is None or value == "":
            continue
        if isinstance(value, bool):
            parts.append(f"{key}={'yes' if value else 'no'}")
        elif isinstance(value, str) and (" " in value or len(value) > 24):
            parts.append(f'{key}="{_clip(value)}"')
        else:
            parts.append(f"{key}={value}")
    logger.info("title timing %s", " ".join(parts))


@dataclass
class _Prepared:
    native: NativeDocument
    title: str
    band: TitleBand
    headers: list[str]
    excerpt: str
    method: str
    title_jpeg: bytes = field(default=b"")
    page_read_locally: bool = False
    layout_source: str = ""
    timings: dict[str, int] = field(default_factory=dict)


def _prepare(
    data: bytes,
    filename: Optional[str],
    max_pages: Optional[int],
) -> _Prepared:
    name = filename or "document.pdf"
    timings: dict[str, int] = {}

    started = time.perf_counter()
    native = extract_pdf(data, max_pages=max_pages)
    timings["native_extract"] = _ms(started)
    _log_step(
        name,
        "native_extract",
        timings["native_extract"],
        pages=native.page_count,
        native_text=native.has_native_text,
    )

    title = ""
    band = TitleBand()
    headers: list[str] = []
    excerpt = ""
    method = "ocr"
    layout_source = ""

    if native.has_native_text:
        started = time.perf_counter()
        title, layout_source, band = resolve_document_title(native, filename=filename)
        headers = [h["text"] for h in list_headers(native, title=title)]
        excerpt = opening_excerpt(native.pages)
        method = "native"
        timings["layout_title"] = _ms(started)
        _log_step(
            name,
            "layout_title",
            timings["layout_title"],
            source=layout_source,
            usable=title_is_usable(title),
            title=title,
        )
    else:
        _log_step(name, "layout_title", skipped="no_native_text")

    jpeg = b""
    page_read_locally = False
    native_title_was_usable = title_is_usable(title)
    if needs_ocr_title(native, band):
        started = time.perf_counter()
        ocr = recover_title(data, need_excerpt=not excerpt_is_usable(excerpt))
        timings["ocr"] = _ms(started)
        page_read_locally = ocr.page_read
        if title_is_usable(ocr.title) and not native_title_was_usable:
            title = ocr.title
            if ocr.band.lines:
                band = ocr.band
        if ocr.excerpt and not excerpt_is_usable(excerpt):
            excerpt = ocr.excerpt
        jpeg = ocr.jpeg
        _log_step(
            name,
            "ocr",
            timings["ocr"],
            usable=title_is_usable(title),
            page_read=ocr.page_read,
            title=title,
        )
        if not jpeg and not title_is_usable(title):
            started = time.perf_counter()
            jpeg = render_title_jpeg(data)
            timings["title_jpeg"] = _ms(started)
            _log_step(name, "title_jpeg", timings["title_jpeg"], bytes=len(jpeg))
        else:
            _log_step(name, "title_jpeg", skipped="not_needed")
    else:
        _log_step(name, "ocr", skipped="readable_native_text")
        _log_step(name, "title_jpeg", skipped="not_needed")

    return _Prepared(
        native=native,
        title=title or "Untitled document",
        band=band,
        headers=headers,
        excerpt=excerpt,
        method=method,
        title_jpeg=jpeg,
        page_read_locally=page_read_locally,
        layout_source=layout_source,
        timings=timings,
    )


def _lean(resp: ExtractResponse, title_only: bool) -> ExtractResponse:
    """Drop page text when a caller only needs the heading."""
    if not title_only:
        return resp
    resp.content = ""
    resp.pages = []
    resp.headers = []
    resp.headers_listed = []
    return resp


async def extract_document(
    data: bytes,
    filename: Optional[str] = None,
    *,
    http: Optional[httpx.AsyncClient] = None,
    max_pages: Optional[int] = None,
    title_only: bool = False,
) -> ExtractResponse:
    started = time.perf_counter()
    elapsed = lambda: int((time.perf_counter() - started) * 1000)
    name = filename or "document.pdf"
    _log_step(name, "start", 0, bytes=len(data))

    wait_started = time.perf_counter()
    prep = await run_extraction(_prepare, data, filename, max_pages)
    prepare_ms = _ms(wait_started)
    inner_ms = sum(prep.timings.values())
    wait_ms = max(0, prepare_ms - inner_ms)
    prep.timings["worker_wait"] = wait_ms
    _log_step(name, "worker_wait", wait_ms)

    native = prep.native

    async def _run_title(client: httpx.AsyncClient) -> tuple[str, str]:
        readable_native = native_text_is_readable(native, prep.band)
        has_text = (
            readable_native
            or title_is_usable(prep.title)
            or excerpt_is_usable(prep.excerpt)
        )
        if has_text:
            groq_started = time.perf_counter()
            heuristic = prep.title if title_is_usable(prep.title) else (prep.band.text or "")
            title, source = await pick_pdf_title(
                client,
                filename=filename or "document.pdf",
                heuristic_title=heuristic,
                band=prep.band,
                headers=prep.headers,
                excerpt=prep.excerpt,
            )
            groq_ms = _ms(groq_started)
            prep.timings["groq_text"] = groq_ms
            _log_step(name, "groq_text", groq_ms, source=source, title=title)
            # Digital PDFs keep the text path even when layout/Groq did not
            # lock a heading. Vision is only for pages with no readable text.
            if readable_native or title_is_usable(title):
                reason = "readable_native_text" if readable_native else "text_title_usable"
                _log_step(name, "groq_vision", skipped=reason)
                return title, source
        else:
            _log_step(name, "groq_text", skipped="no_readable_text")
            title, source = prep.title, "heuristic"
        if prep.title_jpeg:
            groq_started = time.perf_counter()
            vision = await pick_title_from_page_image(
                client,
                jpeg=prep.title_jpeg,
                filename=filename or "document.pdf",
                native_title=title or prep.title,
                excerpt=prep.excerpt,
                patient=not prep.page_read_locally,
                candidates=prep.band.candidates or ([title] if title else []),
            )
            groq_ms = _ms(groq_started)
            prep.timings["groq_vision"] = groq_ms
            _log_step(
                name,
                "groq_vision",
                groq_ms,
                found=bool(vision),
                title=vision or title or prep.title,
            )
            if vision:
                return vision, "groq"
        else:
            _log_step(name, "groq_vision", skipped="no_page_image")
        return title or prep.title, source if has_text else "heuristic"

    title = prep.title
    title_source = "visual"
    if http is None:
        timeout = get_settings().groq_timeout
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=8.0)) as owned:
            title, title_source = await _run_title(owned)
    else:
        title, title_source = await _run_title(http)
    if title_source == "heuristic":
        title_source = "visual"
    title = freeze_title(title) or title

    total_ms = elapsed()
    _log_step(
        name,
        "done",
        total_ms,
        method=prep.method,
        source=title_source,
        layout=prep.layout_source or "-",
        pages=native.page_count,
        native_extract=prep.timings.get("native_extract", 0),
        layout_title=prep.timings.get("layout_title", "-"),
        ocr=prep.timings.get("ocr", "-"),
        groq_text=prep.timings.get("groq_text", "-"),
        groq_vision=prep.timings.get("groq_vision", "-"),
        title=title,
    )

    if not native.has_native_text and not title_is_usable(title):
        return _lean(
            ExtractResponse(
                ok=False,
                method="ocr",
                message=NO_OCR,
                filename=filename,
                page_count=native.page_count,
                elapsed_ms=total_ms,
            ),
            title_only,
        )

    if not native.has_native_text:
        pages = []
        if prep.excerpt:
            pages = [PageText(page=1, text=prep.excerpt, char_count=len(prep.excerpt))]
        headers = list_headers(native, title=title) if native.pages else []
        return _lean(
            ExtractResponse(
                ok=True,
                method="ocr",
                filename=filename,
                page_count=native.page_count,
                title=title,
                title_source=title_source,
                headers=[HeaderItem(**h) for h in headers],
                headers_listed=headers_listed(headers),
                content=prep.excerpt,
                pages=pages,
                elapsed_ms=total_ms,
            ),
            title_only,
        )

    headers = list_headers(native, title=title)
    return _lean(
        ExtractResponse(
            ok=True,
            method="native",
            filename=filename,
            page_count=native.page_count,
            title=title,
            title_source=title_source,
            headers=[HeaderItem(**h) for h in headers],
            headers_listed=headers_listed(headers),
            content=native.content,
            pages=[
                PageText(page=p.page, text=p.text, char_count=p.char_count)
                for p in native.pages
            ],
            elapsed_ms=total_ms,
        ),
        title_only,
    )


__all__ = ["extract_document", "NativePdfError", "EncryptedPdfError"]
