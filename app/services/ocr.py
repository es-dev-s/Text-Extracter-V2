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
    is_citation_line,
    is_cover_chrome,
    is_garbled_text,
    is_journal_label,
    _is_noisy_ocr_title,
    _is_print_header_line,
    is_known_heading,
    is_plausible_title,
    join_title_lines,
    _is_tag_pill_line,
    _is_byline_meta_line,
    _is_web_chrome_line,
    looks_like_author_line,
    _strip_print_chrome_block,
    normalize,
)
from .native import Line, NativeDocument

logger = logging.getLogger("engine.ocr")
MAX_CHROME_BLOCK_LINES = 8  
NO_OCR = "No OCR"

# A title never stops on these; body prose caught mid-sentence usually does.
_SENTENCE_TAILS = frozenset(
    """
    and or of for the a an to with in on by from into using via at as over under
    be is are was were been has have had do does did will would shall should can
    could must may might that which who whom whose this these those it its they
    them their we our you your he she his her not but so than then when while
    """.split()
)

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
    # True when Tesseract read the page. A page read locally but holding no
    # title is title-less, so the vision fallback need not be retried.
    page_read: bool = False


@lru_cache(maxsize=1)
def tesseract_available() -> bool:
    return shutil.which("tesseract") is not None


def ocr_backend() -> str:
    return "tesseract" if tesseract_available() else "disabled"


def title_is_usable(text: str) -> bool:
    t = normalize(text or "")
    if not t or t.lower() in {"untitled document", "untitled", "no ocr", "no title"}:
        return False
    words = t.split()
    if words and words[-1].lower().strip("-,:;") in _SENTENCE_TAILS:
        return False
    return is_plausible_title(t)



def native_text_is_readable(native: NativeDocument, band: TitleBand | None = None) -> bool:
    """True when the PDF has a usable text layer. A weak title is not a scan."""
    if not native.has_native_text:
        return False
    if band and band.text and is_garbled_text(band.text):
        return False
    # If the title band itself is present and not garbled, trust it — it's a
    # more targeted signal than a blind prefix of raw page text, which can
    # start with non-English masthead/journal-label text on multilingual
    # papers and wrongly look "unreadable" by a Latin-ratio check.
    if band and band.text:
        return True
    sample = ""
    if native.pages:
        sample = (native.pages[0].text or "")[:180]
    if not sample:
        sample = (native.content or "")[:180]
    if sample and is_garbled_text(sample):
        return False
    return True

def needs_ocr_title(native: NativeDocument, band: TitleBand) -> bool:
    """Rasterize page 1 only when embedded text is missing or unreadable.

    Layout failing to pick a title is not a scan: Groq text still has the
    excerpt. CID dumps look native but are not readable, so those still OCR.
    """
    return not native_text_is_readable(native, band)

def _is_chrome_line(text: str) -> bool:
    """Any of the known non-title UI patterns — used only for the
    contiguous chrome-block skip right after a print-header line."""
    return (
        _is_web_chrome_line(text)
        or _is_tag_pill_line(text)
        or _is_byline_meta_line(text)
        or _is_noisy_ocr_title(text)
    )

 # safety cap; never skip more than this many


# def _strip_print_chrome_block(lines: list[str]) -> list[str]:
    # """After a browser print-header line, Medium/Substack-style pages stack
    # navbar, tag-pills, and byline chrome before the real content. Skip that
    # whole contiguous run — but only lines that actually match a known
    # chrome pattern, stopping at the first line that doesn't. This can't
    # remove real content: every skipped line already matches an existing
    # chrome/noise detector, it's just applied as a block instead of relying
    # on each detector to independently reject it later."""
    # for i, line in enumerate(lines):
    #     if not _is_print_header_line(line):
    #         continue
    #     j = i + 1
    #     limit = min(len(lines), i + 1 + MAX_CHROME_BLOCK_LINES)
    #     while j < limit and _is_chrome_line(lines[j]):
    #         j += 1
    #     del lines[i:j]
    #     break
    # return lines

def excerpt_is_usable(text: str) -> bool:
    t = (text or "").strip()
    if len(t) < 40:
        return False
    return not is_garbled_text(t[:180])


# def recover_title(data: bytes, *, need_excerpt: bool = False) -> OcrTitle:
#     """Render the top of page 1 and OCR it. Never walks the whole PDF."""
#     result = OcrTitle()
#     if not data:
#         return result
#     try:
#         doc = fitz.open(stream=data, filetype="pdf")
#     except Exception:
#         logger.warning("OCR could not open PDF")
#         return result
#     try:
#         if doc.needs_pass or doc.page_count < 1:
#             return result
#         page = doc[1]
#         clip = fitz.Rect(0, 0, page.rect.width, page.rect.height * TITLE_CROP_FRACTION)
#         matrix = fitz.Matrix(RENDER_SCALE, RENDER_SCALE)
#         pix = page.get_pixmap(matrix=matrix, clip=clip, alpha=False)
#         try:
#             png = pix.tobytes("png")

#             crop_text = _tesseract_image(png)

#             print("\n" + "=" * 100)
#             print("OCR RAW TEXT")
#             print("=" * 100)
#             print(crop_text)
#             print("=" * 100)

#             result.page_read = len((crop_text or "").split()) >= 12

#             result.title, result.band = _title_from_ocr(crop_text)

#             print("\n" + "=" * 100)
#             print("OCR EXTRACTED TITLE")
#             print("=" * 100)
#             print(f"TITLE: {result.title!r}")
#             print(f"BAND:  {result.band.text!r}")

#             for i, ln in enumerate(result.band.lines):
#                 print(
#                     f"[{i}] text={ln.text!r} "
#                     f"size={ln.size} "
#                     f"bold={ln.bold} "
#                     f"bbox=({ln.x0:.1f}, {ln.y0:.1f}, "
#                     f"{ln.x1:.1f}, {ln.y1:.1f})"
#                 )

#             print("=" * 100)
#             if not title_is_usable(result.title):
#                 result.jpeg = pix.tobytes("jpeg", jpg_quality=JPEG_QUALITY)
#             del png
#         finally:
#             pix = None

#         if need_excerpt and tesseract_available():
#             full = page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
#             try:
#                 result.excerpt = _clip_excerpt(_tesseract_image(full.tobytes("png")))
#             finally:
#                 full = None
#     except Exception:
#         logger.warning("OCR render failed", exc_info=True)
#     finally:
#         doc.close()
#     return result

def recover_title(data: bytes, *, need_excerpt: bool = False) -> OcrTitle:
    """Render the top of the first few pages, OCR each, and keep the first clean title."""
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

        max_pages_to_try = min(3, doc.page_count)
        fallback_title, fallback_band, fallback_jpeg = "", TitleBand(), b""
        any_page_read = False

        for page_index in range(max_pages_to_try):
            page = doc[page_index]
            clip = fitz.Rect(0, 0, page.rect.width, page.rect.height * TITLE_CROP_FRACTION)
            matrix = fitz.Matrix(RENDER_SCALE, RENDER_SCALE)
            pix = page.get_pixmap(matrix=matrix, clip=clip, alpha=False)
            try:
                png = pix.tobytes("png")

                crop_text = _tesseract_image(png)

                print("\n" + "=" * 100)
                print(f"OCR RAW TEXT (page {page_index})")
                print("=" * 100)
                print(crop_text)
                print("=" * 100)

                page_read = len((crop_text or "").split()) >= 12
                any_page_read = any_page_read or page_read

                title, band = _title_from_ocr(crop_text)

                print("\n" + "=" * 100)
                print(f"OCR EXTRACTED TITLE (page {page_index})")
                print("=" * 100)
                print(f"TITLE: {title!r}")
                print(f"BAND:  {band.text!r}")

                for i, ln in enumerate(band.lines):
                    print(
                        f"[{i}] text={ln.text!r} "
                        f"size={ln.size} "
                        f"bold={ln.bold} "
                        f"bbox=({ln.x0:.1f}, {ln.y0:.1f}, "
                        f"{ln.x1:.1f}, {ln.y1:.1f})"
                    )
                print("=" * 100)

                # <-- THIS is where it goes: replaces the old
                # "if not title_is_usable(title): jpeg = ..." / score block
                is_clean = (
                    title_is_usable(title)
                    and not is_garbled_text(title)
                    and not _is_noisy_ocr_title(title)
                )

                if is_clean:
                    result.title, result.band = title, band
                    del png
                    break  # first clean title wins, stop scanning further pages

                if not fallback_title and title:
                    fallback_title, fallback_band = title, band
                    fallback_jpeg = pix.tobytes("jpeg", jpg_quality=JPEG_QUALITY)
                elif not title:
                    fallback_jpeg = fallback_jpeg or pix.tobytes("jpeg", jpg_quality=JPEG_QUALITY)

                del png
            finally:
                pix = None
        else:
            # loop finished without a clean title anywhere — use the fallback
            result.title, result.band, result.jpeg = fallback_title, fallback_band, fallback_jpeg

        result.page_read = any_page_read

        if need_excerpt and tesseract_available():
            full = doc[0].get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
            try:
                result.excerpt = _clip_excerpt(_tesseract_image(full.tobytes("png")))
            finally:
                full = None
    except Exception:
        logger.warning("OCR render failed", exc_info=True)
    finally:
        doc.close()
    return result
    """Render the top of the first few pages, OCR each, and keep the best-looking title."""
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

        max_pages_to_try = min(3, doc.page_count)
        best = None  # (score, page_index, title, band, jpeg)
        any_page_read = False

        for page_index in range(max_pages_to_try):
            page = doc[page_index]
            clip = fitz.Rect(0, 0, page.rect.width, page.rect.height * TITLE_CROP_FRACTION)
            matrix = fitz.Matrix(RENDER_SCALE, RENDER_SCALE)
            pix = page.get_pixmap(matrix=matrix, clip=clip, alpha=False)
            try:
                png = pix.tobytes("png")

                crop_text = _tesseract_image(png)

                print("\n" + "=" * 100)
                print(f"OCR RAW TEXT (page {page_index})")
                print("=" * 100)
                print(crop_text)
                print("=" * 100)

                page_read = len((crop_text or "").split()) >= 12
                any_page_read = any_page_read or page_read

                title, band = _title_from_ocr(crop_text)

                print("\n" + "=" * 100)
                print(f"OCR EXTRACTED TITLE (page {page_index})")
                print("=" * 100)
                print(f"TITLE: {title!r}")
                print(f"BAND:  {band.text!r}")

                for i, ln in enumerate(band.lines):
                    print(
                        f"[{i}] text={ln.text!r} "
                        f"size={ln.size} "
                        f"bold={ln.bold} "
                        f"bbox=({ln.x0:.1f}, {ln.y0:.1f}, "
                        f"{ln.x1:.1f}, {ln.y1:.1f})"
                    )
                print("=" * 100)

                jpeg = None
                if not title_is_usable(title):
                    jpeg = pix.tobytes("jpeg", jpg_quality=JPEG_QUALITY)

                if title_is_usable(title):
                    # score by the largest font size in the band — bigger text
                    # is a much stronger "this is a title" signal than which
                    # page it happened to be found on
                    score = max((ln.size for ln in band.lines), default=0)
                    if best is None or score > best[0]:
                        best = (score, page_index, title, band, jpeg)

                del png
            finally:
                pix = None

        result.page_read = any_page_read
        if best is not None:
            _, _, result.title, result.band, result.jpeg = best
        else:
            # nothing usable anywhere — fall back to the first page's attempt
            # so callers still get *something* to inspect
            first_page = doc[0]
            clip = fitz.Rect(0, 0, first_page.rect.width, first_page.rect.height * TITLE_CROP_FRACTION)
            pix = first_page.get_pixmap(matrix=fitz.Matrix(RENDER_SCALE, RENDER_SCALE), clip=clip, alpha=False)
            try:
                crop_text = _tesseract_image(pix.tobytes("png"))
                result.title, result.band = _title_from_ocr(crop_text)
                result.jpeg = pix.tobytes("jpeg", jpg_quality=JPEG_QUALITY)
            finally:
                pix = None

        if need_excerpt and tesseract_available():
            full = doc[0].get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
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


def _ends_with_wrap(text: str) -> bool:
    words = normalize(text).split()
    return bool(words) and words[-1].lower().strip("-,:;") in _SENTENCE_TAILS


def _skip_ocr_line(text: str) -> bool:
    t = normalize(text)
    if not t:
        return True
    if not any(c.isalnum() for c in t):
        return True
    if _is_print_header_line(t):
        return True
    if is_known_heading(t) or is_cover_chrome(t) or is_journal_label(t):
        return True
    if looks_like_author_line(t) or is_citation_line(t):
        return True
    if "@" in t or t.lower().startswith(("copyright", "proceedings of", "issn", "doi")):
        return True
    if t.lower().startswith(("department of", "email:", "keywords")):
        return True
    return False
def _is_banner_tail(text: str, after_banner: bool) -> bool:
    """Second line of a wrapped masthead, e.g. 'MANUFACTURING ENGINEERING'."""
    t = normalize(text)
    return after_banner and t.isupper() and len(t.split()) <= 4


def _title_from_ocr(raw: str) -> tuple[str, TitleBand]:
    """Pick the first block of OCR lines that reads like the printed title."""
    lines = [normalize(line) for line in (raw or "").splitlines()]
    lines = [line for line in lines if line]
    collected: list[str] = []
    after_banner = False
    lines = _strip_print_chrome_block(lines)
    for i, text in enumerate(lines[:25]):
        if _skip_ocr_line(text):
            after_banner = is_cover_chrome(text) or is_journal_label(text)
            continue
        if _is_banner_tail(text, after_banner):
            continue
        after_banner = False

        group = [text]
        while len(group) < 3 and i + len(group) < len(lines):
            nxt = lines[i + len(group)]
            if _skip_ocr_line(nxt):
                break
            joined = " ".join(group)
            incomplete = not title_is_usable(joined) or _ends_with_wrap(joined)
            # A one- or two-word line under a complete title is its wrapped tail.
            tail = (
                title_is_usable(joined)
                and len(nxt.split()) <= 3
                and nxt[:1].isupper()
                and not is_plausible_title(nxt)
            )
            if not incomplete and not tail:
                break
            group.append(nxt)
        joined = " ".join(group)
        if title_is_usable(joined):
            collected = group
            break

    if not collected:
        return "", TitleBand()
    lines_out: list[Line] = []
    y = 48.0
    for piece in collected:
        lines_out.append(
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
    joined = join_title_lines(lines_out)
    band = TitleBand(lines=lines_out, text=joined)
    title = joined if title_is_usable(joined) else ""
    return title, band
