"""Native PDF text extraction via PyMuPDF (single textpage pass per page)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

import pymupdf as fitz

# A page with fewer than this many embedded characters is treated as scanned.
DIGITAL_TEXT_MIN_CHARS = 20
# Whole document is scanned if native yield is below this.
DOC_MIN_CHARS = 50

ITALIC_FLAG = 1 << 1
BOLD_FLAG = 1 << 4


@dataclass
class Line:
    text: str
    page: int
    size: float
    bold: bool
    italic: bool
    x0: float
    y0: float
    x1: float
    y1: float
    page_width: float
    page_height: float
    font: str = ""

    @property
    def height(self) -> float:
        return max(self.size, self.y1 - self.y0)

    @property
    def left(self) -> float:
        return self.x0

    @property
    def top(self) -> float:
        return self.y0

    @property
    def width(self) -> float:
        return self.x1 - self.x0


@dataclass
class PageExtract:
    page: int
    width: float
    height: float
    text: str
    lines: List[Line] = field(default_factory=list)
    char_count: int = 0
    digital: bool = True


@dataclass
class NativeDocument:
    page_count: int
    meta_title: Optional[str]
    toc: List[Tuple[int, str, int]]
    pages: List[PageExtract]
    content: str
    has_native_text: bool
    encrypted: bool = False


class NativePdfError(ValueError):
    """PDF could not be opened or is not a usable PDF."""


class EncryptedPdfError(NativePdfError):
    """PDF requires a password."""


def _span_bold(span: dict) -> bool:
    flags = int(span.get("flags") or 0)
    font = str(span.get("font") or "").lower()
    return bool(flags & BOLD_FLAG) or ("bold" in font)


def _span_italic(span: dict) -> bool:
    flags = int(span.get("flags") or 0)
    font = str(span.get("font") or "").lower()
    return bool(flags & ITALIC_FLAG) or ("italic" in font) or ("oblique" in font)


def _line_from_spans(
    spans: Sequence[dict],
    page_no: int,
    page_width: float,
    page_height: float,
) -> Optional[Line]:
    parts: List[str] = []
    scored: List[Tuple[int, float, bool, bool, str]] = []
    x0 = y0 = 1e12
    x1 = y1 = -1.0
    for span in spans:
        text = span.get("text") or ""
        if not text:
            continue
        parts.append(text)
        bbox = span.get("bbox") or (0, 0, 0, 0)
        x0 = min(x0, float(bbox[0]))
        y0 = min(y0, float(bbox[1]))
        x1 = max(x1, float(bbox[2]))
        y1 = max(y1, float(bbox[3]))
        weight = len(text.strip())
        if weight == 0:
            continue
        scored.append((
            weight,
            float(span.get("size") or 0.0),
            _span_bold(span),
            _span_italic(span),
            str(span.get("font") or ""),
        ))
    text = "".join(parts).strip()
    if not text:
        return None
    if scored:
        _w, size, bold, italic, font = max(scored, key=lambda row: (row[0], row[1]))
    else:
        size, bold, italic, font = 0.0, False, False, ""
    return Line(
        text=text,
        page=page_no,
        size=size,
        bold=bold,
        italic=italic,
        x0=x0,
        y0=y0,
        x1=x1,
        y1=y1,
        page_width=page_width,
        page_height=page_height,
        font=font,
    )


def _extract_page(page, page_no: int) -> PageExtract:
    rect = page.rect
    width, height = float(rect.width), float(rect.height)
    tp = page.get_textpage(flags=fitz.TEXTFLAGS_TEXT)
    try:
        try:
            plain = tp.extractText(sort=True) or ""
            data = tp.extractDICT(sort=True) or {}
        except TypeError:
            plain = tp.extractText() or ""
            data = tp.extractDICT() or {}
    finally:
        tp = None

    lines: List[Line] = []
    for block in data.get("blocks", []):
        if block.get("type", 0) != 0:
            continue
        for raw_line in block.get("lines", []):
            parsed = _line_from_spans(
                raw_line.get("spans") or [],
                page_no,
                width,
                height,
            )
            if parsed is not None:
                lines.append(parsed)

    reconstructed = "\n".join(ln.text for ln in lines)
    # Prefer the longer extract so clipped/hidden spans are not dropped.
    text = plain.strip() if len(plain.strip()) >= len(reconstructed.strip()) else reconstructed
    char_count = len(text.strip())
    digital = char_count >= DIGITAL_TEXT_MIN_CHARS
    return PageExtract(
        page=page_no,
        width=width,
        height=height,
        text=text,
        lines=lines,
        char_count=char_count,
        digital=digital,
    )


def extract_pdf(data: bytes) -> NativeDocument:
    if not data:
        raise NativePdfError("Empty file")
    if not data.lstrip().startswith(b"%PDF-"):
        raise NativePdfError("File is not a PDF")

    try:
        doc = fitz.open(stream=data, filetype="pdf")
    except Exception as exc:
        raise NativePdfError(f"Cannot open PDF: {exc}") from exc

    try:
        if doc.needs_pass:
            raise EncryptedPdfError("PDF is password-protected")
        if doc.is_encrypted and doc.needs_pass:
            raise EncryptedPdfError("PDF is password-protected")

        meta = doc.metadata or {}
        meta_title = (meta.get("title") or "").strip() or None
        try:
            toc = [(int(lvl), str(title).strip(), int(page)) for lvl, title, page in (doc.get_toc() or []) if str(title).strip()]
        except Exception:
            toc = []

        pages = [_extract_page(page, i + 1) for i, page in enumerate(doc)]
    finally:
        doc.close()

    parts: List[str] = []
    total_chars = 0
    for p in pages:
        total_chars += p.char_count
        if len(pages) == 1:
            parts.append(p.text)
        else:
            parts.append(f"--- Page {p.page} ---\n{p.text}".rstrip())
    content = "\n\n".join(parts).strip()
    has_native = total_chars >= DOC_MIN_CHARS

    return NativeDocument(
        page_count=len(pages),
        meta_title=meta_title,
        toc=toc,
        pages=pages,
        content=content,
        has_native_text=has_native,
    )
