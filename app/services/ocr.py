"""Lightweight title OCR: Tesseract on a page-1 crop only. No PyTorch."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from functools import lru_cache

import pymupdf as fitz

from .headings import (
    TitleBand,
    is_cover_chrome,
    is_garbled_text,
    is_journal_label,
    is_known_heading,
    is_plausible_title,
    join_title_lines,
    looks_like_author_line,
    normalize,
)
from .native import Line, NativeDocument

logger = logging.getLogger("engine.ocr")

NO_OCR = "No OCR"

# Locked render settings — same PDF bytes → same pixels → same OCR text.
RENDER_SCALE = 2.0
TITLE_CROP_FRACTION = 0.40
TESSERACT_TIMEOUT = 18
JPEG_QUALITY = 72


@dataclass
class OcrTitle:
    title: str = ""
    excerpt: str = ""
    band: TitleBand = field(default_factory=TitleBand)
    jpeg: bytes = b""


@lru_cache(maxsize=1)
def tesseract_available() -> bool:
    return shutil.which("tesseract") is not None


def ocr_backend() -> str:
    return "tesseract" if tesseract_available() else "disabled"


def title_is_usable(text: str) -> bool:
    t = normalize(text or "")
    if not t or t.lower() in {"untitled document", "untitled", "no ocr", "no title"}:
        return False
    return is_plausible_title(t)


def needs_ocr_title(title: str, native: NativeDocument, band: TitleBand) -> bool:
    """OCR only when layout text cannot yield a real title. Good PDFs skip this."""
    if not native.has_native_text:
        return True
    if band.text and is_garbled_text(band.text):
        return True
    return not title_is_usable(title)


def excerpt_is_usable(text: str) -> bool:
    t = (text or "").strip()
    if len(t) < 40:
        return False
    return not is_garbled_text(t[:180])


def recover_title(data: bytes, *, need_excerpt: bool = False) -> OcrTitle:
    """Render the top of page 1 and OCR it. Never walks the whole PDF."""
    result = OcrTitle()
    if not data:
        return result
    try:
        doc = fitz.open(stream=data, filetype="pdf")
    except Exception:
        logger.warning("OCR could not open PDF")
        return result
    try:
        if doc.needs_pass or doc.page_count < 1:
            return result
        page = doc[0]
        clip = fitz.Rect(0, 0, page.rect.width, page.rect.height * TITLE_CROP_FRACTION)
        matrix = fitz.Matrix(RENDER_SCALE, RENDER_SCALE)
        pix = page.get_pixmap(matrix=matrix, clip=clip, alpha=False)
        try:
            png = pix.tobytes("png")
            crop_text = _tesseract_image(png)
            result.title, result.band = _title_from_ocr(crop_text)
            if not title_is_usable(result.title):
                result.jpeg = pix.tobytes("jpeg", jpg_quality=JPEG_QUALITY)
            del png
        finally:
            pix = None

        if need_excerpt and tesseract_available():
            full = page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
            try:
                result.excerpt = _clip_excerpt(_tesseract_image(full.tobytes("png")))
            finally:
                full = None
    except Exception:
        logger.warning("OCR render failed", exc_info=True)
    finally:
        doc.close()
    return result


def render_title_jpeg(data: bytes) -> bytes:
    """Small JPEG of the title region for Groq vision when Tesseract is missing."""
    if not data:
        return b""
    try:
        doc = fitz.open(stream=data, filetype="pdf")
    except Exception:
        return b""
    try:
        if doc.needs_pass or doc.page_count < 1:
            return b""
        page = doc[0]
        clip = fitz.Rect(0, 0, page.rect.width, page.rect.height * TITLE_CROP_FRACTION)
        pix = page.get_pixmap(matrix=fitz.Matrix(RENDER_SCALE, RENDER_SCALE), clip=clip, alpha=False)
        try:
            return pix.tobytes("jpeg", jpg_quality=JPEG_QUALITY)
        finally:
            pix = None
    except Exception:
        logger.warning("title JPEG render failed")
        return b""
    finally:
        doc.close()


def run_ocr(_data: bytes, *, filename: str | None = None) -> str:
    recovered = recover_title(_data)
    return recovered.title or NO_OCR


def _tesseract_image(image_bytes: bytes) -> str:
    if not image_bytes or not tesseract_available():
        return ""
    env = os.environ.copy()
    env["OMP_THREAD_LIMIT"] = "1"
    cmd = ["tesseract", "stdin", "stdout", "--oem", "1", "--psm", "6", "-l", "eng"]
    try:
        proc = subprocess.run(
            cmd,
            input=image_bytes,
            capture_output=True,
            timeout=TESSERACT_TIMEOUT,
            check=False,
            env=env,
        )
        if proc.returncode == 0 and proc.stdout:
            return proc.stdout.decode("utf-8", errors="replace")
    except (subprocess.TimeoutExpired, OSError):
        logger.warning("tesseract stdin failed; trying tempfile")
    return _tesseract_tempfile(image_bytes, env)


def _tesseract_tempfile(image_bytes: bytes, env: dict) -> str:
    path = ""
    try:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp.write(image_bytes)
            path = tmp.name
        proc = subprocess.run(
            ["tesseract", path, "stdout", "--oem", "1", "--psm", "6", "-l", "eng"],
            capture_output=True,
            timeout=TESSERACT_TIMEOUT,
            check=False,
            env=env,
        )
        if proc.returncode == 0 and proc.stdout:
            return proc.stdout.decode("utf-8", errors="replace")
    except (subprocess.TimeoutExpired, OSError):
        logger.warning("tesseract unavailable or timed out")
    finally:
        if path:
            try:
                os.unlink(path)
            except OSError:
                pass
    return ""


def _clip_excerpt(text: str, limit: int = 1000) -> str:
    blob = " ".join((text or "").split())
    if len(blob) <= limit:
        return blob
    cut = blob[:limit]
    if blob[limit].isalnum() and cut[-1:].isalnum():
        cut = cut.rsplit(" ", 1)[0]
    return cut.strip()


def _skip_ocr_line(text: str) -> bool:
    t = normalize(text)
    if not t:
        return True
    if is_known_heading(t) or is_cover_chrome(t) or is_journal_label(t):
        return True
    if looks_like_author_line(t):
        return True
    if "@" in t or t.lower().startswith(("copyright", "proceedings of", "issn", "doi")):
        return True
    if t.lower().startswith(("department of", "email:", "keywords")):
        return True
    return False


def _title_from_ocr(raw: str) -> tuple[str, TitleBand]:
    collected: list[str] = []
    for line in (raw or "").splitlines():
        piece = normalize(line)
        if _skip_ocr_line(piece):
            if collected:
                break
            continue
        collected.append(piece)
        joined = " ".join(collected)
        if title_is_usable(joined) and len(collected) >= 1 and not joined.lower().endswith(
            (" and", " of", " for", " the", " a")
        ):
            if len(joined) >= 24 or len(collected) >= 2:
                break
        if len(collected) >= 4:
            break
    if not collected:
        return "", TitleBand()
    lines: list[Line] = []
    y = 48.0
    for piece in collected:
        lines.append(
            Line(
                text=piece,
                page=1,
                size=16.0,
                bold=True,
                italic=False,
                x0=40.0,
                y0=y,
                x1=520.0,
                y1=y + 18.0,
                page_width=612.0,
                page_height=792.0,
            )
        )
        y += 22.0
    joined = join_title_lines(lines)
    band = TitleBand(lines=lines, text=joined)
    title = joined if title_is_usable(joined) else ""
    return title, band
