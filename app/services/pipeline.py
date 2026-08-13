"""Try native PDF extraction first; fall back to OCR (currently a stub)."""

from __future__ import annotations

import asyncio
import time
from typing import Optional

import httpx

from ..schemas import ExtractResponse, HeaderItem, PageText
from .groq_title import opening_excerpt, pick_pdf_title
from .headings import TitleBand, headers_listed, list_headers, resolve_document_title
from .native import EncryptedPdfError, NativeDocument, NativePdfError, extract_pdf
from .ocr import NO_OCR, run_ocr


def _native_pass(
    data: bytes, filename: Optional[str]
) -> tuple[NativeDocument, str, TitleBand, list[str], str]:
    native = extract_pdf(data)
    if not native.has_native_text:
        run_ocr(data, filename=filename)
        return native, "", TitleBand(), [], ""
    title, _source, band = resolve_document_title(native, filename=filename)
    headers = list_headers(native, title=title)
    excerpt = opening_excerpt(native.pages)
    return native, title, band, [h["text"] for h in headers], excerpt


async def extract_document(
    data: bytes,
    filename: Optional[str] = None,
    *,
    http: Optional[httpx.AsyncClient] = None,
) -> ExtractResponse:
    started = time.perf_counter()
    elapsed = lambda: int((time.perf_counter() - started) * 1000)

    native, heuristic_title, band, header_texts, excerpt = await asyncio.to_thread(
        _native_pass, data, filename
    )

    if not native.has_native_text:
        return ExtractResponse(
            ok=False,
            method="ocr",
            message=NO_OCR,
            filename=filename,
            page_count=native.page_count,
            elapsed_ms=elapsed(),
        )

    title = heuristic_title or band.text or "Untitled document"
    title_source = "visual"
    groq_kwargs = dict(
        filename=filename or "document.pdf",
        heuristic_title=title,
        band=band,
        headers=header_texts,
        excerpt=excerpt,
    )
    if http is None:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=8.0)) as owned:
            title, title_source = await pick_pdf_title(owned, **groq_kwargs)
    else:
        title, title_source = await pick_pdf_title(http, **groq_kwargs)
    if title_source == "heuristic":
        title_source = "visual"

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
