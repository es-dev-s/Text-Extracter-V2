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


def _join_fragment_texts(parts: Sequence[str]) -> str:
    """Join word-level PDF fragments without inventing punctuation."""
    out = ""
    for raw in parts:
        piece = str(raw or "")
        if not piece:
            continue
        if not out:
            out = piece
            continue
        if out.endswith("-") and piece[:1].islower():
            out = out[:-1] + piece
            continue
        if out[-1].isspace() or piece[0].isspace():
            out = out.rstrip() + " " + piece.lstrip()
            continue
        if piece[0] in ",.;:!?)]}%":
            out += piece
            continue
        if out[-1] in "([/":
            out += piece
            continue
        out += " " + piece
    return out.strip()


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


def _same_baseline(a: Line, b: Line) -> bool:
    ay = (a.y0 + a.y1) / 2.0
    by = (b.y0 + b.y1) / 2.0
    size = min(a.size or 8.0, b.size or 8.0, 14.0)
    return abs(ay - by) <= max(2.4, size * 0.42)


def _column_gap_lines(prev: Line, nxt: Line) -> bool:
    gap = nxt.x0 - prev.x1
    if gap <= 0:
        return False
    size = max(prev.size, nxt.size, 8.0)
    width = prev.page_width or nxt.page_width or 0.0
    return gap >= max(36.0, width * 0.08, size * 4.0)


def _merge_line_group(group: Sequence[Line]) -> Line:
    items = sorted(group, key=lambda ln: ln.x0)
    scored: List[Tuple[int, Line]] = []
    for ln in items:
        scored.append((max(len((ln.text or "").strip()), 1), ln))
    _w, style = max(scored, key=lambda row: (row[0], row[1].size))
    return Line(
        text=_join_fragment_texts([ln.text for ln in items]),
        page=style.page,
        size=style.size,
        bold=style.bold,
        italic=style.italic,
        x0=min(ln.x0 for ln in items),
        y0=min(ln.y0 for ln in items),
        x1=max(ln.x1 for ln in items),
        y1=max(ln.y1 for ln in items),
        page_width=style.page_width,
        page_height=style.page_height,
        font=style.font,
    )


def _merge_reading_lines(lines: Sequence[Line]) -> List[Line]:
    """Rebuild print lines when PyMuPDF emits one word per line."""
    if len(lines) <= 1:
        return list(lines)
    ordered = sorted(lines, key=lambda ln: (ln.y0, ln.x0))
    rows: List[List[Line]] = [[ordered[0]]]
    for ln in ordered[1:]:
        if any(_same_baseline(prev, ln) for prev in rows[-1]):
            rows[-1].append(ln)
        else:
            rows.append([ln])
    merged: List[Line] = []
    for row in rows:
        row.sort(key=lambda ln: ln.x0)
        segments: List[List[Line]] = [[row[0]]]
        for ln in row[1:]:
            if _column_gap_lines(segments[-1][-1], ln):
                segments.append([ln])
            else:
                segments[-1].append(ln)
        for segment in segments:
            merged.append(segment[0] if len(segment) == 1 else _merge_line_group(segment))
    return merged


def _extract_page(page, page_no: int) -> PageExtract:
    rect = page.rect
    width, height = float(rect.width), float(rect.height)
    tp = page.get_textpage(flags=fitz.TEXTFLAGS_TEXT)
    try:
        try:
            plain = tp.extractText(sort=True) or ""
            data = tp.extractDICT(sort=True) or {}
            print("\n" + "=" * 80)
            print(f"PYMUPDF RAW TEXT - PAGE {page_no}")
            print("=" * 80)
            print(plain)

        except TypeError:
            plain = tp.extractText() or ""
            data = tp.extractDICT() or {}
    finally:
        close = getattr(tp, "close", None)
        if callable(close):
            close()

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

    lines = _merge_reading_lines(lines)
    reconstructed = "\n".join(ln.text for ln in lines)
    print("\n" + "=" * 80)
    print(f"RECONSTRUCTED LINES - PAGE {page_no}")
    print("=" * 80)

    for i, ln in enumerate(lines):
        print(
            f"[{i}] text={ln.text!r} "
            f"size={ln.size} "
            f"bold={ln.bold} "
            f"font={ln.font!r} "
            f"bbox=({ln.x0:.1f}, {ln.y0:.1f}, {ln.x1:.1f}, {ln.y1:.1f})"
        )

    print("=" * 80)
    avg_words = (
        sum(len(ln.text.split()) for ln in lines) / len(lines)
        if lines else 0.0
    )
    if avg_words >= 3 and reconstructed.strip():
        text = reconstructed.strip()
    else:
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


def extract_pdf(data: bytes, *, max_pages: Optional[int] = None) -> NativeDocument:
    if not data:
        raise NativePdfError("Empty file")
    if not data.lstrip().startswith(b"%PDF-"):
        raise NativePdfError("File is not a PDF")

    try:
        doc = fitz.open(stream=data, filetype="pdf")
    except Exception:
        raise NativePdfError("Cannot open PDF") from None

    try:
        if doc.needs_pass:
            raise EncryptedPdfError("PDF is password-protected")

        if max_pages is not None and doc.page_count > max_pages:
            raise NativePdfError(f"PDF exceeds {max_pages} page limit")

        meta = doc.metadata or {}
        meta_title = (meta.get("title") or "").strip() or None
        try:
            toc = [
                (int(lvl), str(title).strip(), int(page))
                for lvl, title, page in (doc.get_toc() or [])
                if str(title).strip()
            ]
        except Exception:
            toc = []

        pages = [_extract_page(page, i + 1) for i, page in enumerate(doc)]
    except (EncryptedPdfError, NativePdfError):
        raise
    except Exception:
        raise NativePdfError("Cannot read PDF") from None
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
