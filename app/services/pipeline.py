"""Try native PDF extraction first; OCR only when the title cannot be read."""

from __future__ import annotations

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
    needs_ocr_title,
    recover_title,
    render_title_jpeg,
    title_is_usable,
)


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


def _prepare(
    data: bytes,
    filename: Optional[str],
    max_pages: Optional[int],
) -> _Prepared:
    native = extract_pdf(data, max_pages=max_pages)
    title = ""
    band = TitleBand()
    headers: list[str] = []
    excerpt = ""
    method = "ocr"

    if native.has_native_text:
        title, _source, band = resolve_document_title(native, filename=filename)
        headers = [h["text"] for h in list_headers(native, title=title)]
        excerpt = opening_excerpt(native.pages)
        method = "native"

    jpeg = b""
    page_read_locally = False
    if needs_ocr_title(title, native, band):
        ocr = recover_title(data, need_excerpt=not excerpt_is_usable(excerpt))
        page_read_locally = ocr.page_read
        if title_is_usable(ocr.title):
            title = ocr.title
            if ocr.band.lines:
                band = ocr.band
        if ocr.excerpt and not excerpt_is_usable(excerpt):
            excerpt = ocr.excerpt
        jpeg = ocr.jpeg
        if not jpeg and not title_is_usable(title):
            jpeg = render_title_jpeg(data)

    return _Prepared(
        native=native,
        title=title or "Untitled document",
        band=band,
        headers=headers,
        excerpt=excerpt,
        method=method,
        title_jpeg=jpeg,
        page_read_locally=page_read_locally,
    )


async def extract_document(
    data: bytes,
    filename: Optional[str] = None,
    *,
    http: Optional[httpx.AsyncClient] = None,
    max_pages: Optional[int] = None,
) -> ExtractResponse:
    started = time.perf_counter()
    elapsed = lambda: int((time.perf_counter() - started) * 1000)

    prep = await run_extraction(_prepare, data, filename, max_pages)
    native = prep.native

    async def _run_title(client: httpx.AsyncClient) -> tuple[str, str]:
        if title_is_usable(prep.title):
            title, source = await pick_pdf_title(
                client,
                filename=filename or "document.pdf",
                heuristic_title=prep.title,
                band=prep.band,
                headers=prep.headers,
                excerpt=prep.excerpt,
            )
            return title, source
        if prep.title_jpeg:
            vision = await pick_title_from_page_image(
                client,
                jpeg=prep.title_jpeg,
                filename=filename or "document.pdf",
                native_title=prep.title,
                excerpt=prep.excerpt,
                patient=not prep.page_read_locally,
            )
            if vision:
                return vision, "groq"
        return prep.title, "heuristic"

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

    if not native.has_native_text and not title_is_usable(title):
        return ExtractResponse(
            ok=False,
            method="ocr",
            message=NO_OCR,
            filename=filename,
            page_count=native.page_count,
            elapsed_ms=elapsed(),
        )

    if not native.has_native_text:
        pages = []
        if prep.excerpt:
            pages = [PageText(page=1, text=prep.excerpt, char_count=len(prep.excerpt))]
        headers = list_headers(native, title=title) if native.pages else []
        return ExtractResponse(
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
            elapsed_ms=elapsed(),
        )

    headers = list_headers(native, title=title)
    return ExtractResponse(
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
        elapsed_ms=elapsed(),
    )


__all__ = ["extract_document", "NativePdfError", "EncryptedPdfError"]
