"""Document title and numbered header listing from native PDF layout."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Sequence, Tuple

from .native import Line, NativeDocument, PageExtract


@dataclass
class TitleBand:
    """Largest-font wrapped title block on the first content page."""

    lines: List[Line] = field(default_factory=list)
    text: str = ""
    context: str = ""

MAX_TITLE_LEN = 240
MAX_HEADER_LEN = 160
VISUAL_TOP_FRACTION = 0.52
VISUAL_MIN_SCORE = 1.15
MIN_SCAN_PAGE_WORDS = 80
MERGE_GAP_RATIO = 2.2
TITLE_BAND_MAX_LINES = 5

_GENERIC_TITLES = {
    "untitled", "untitled document", "untitled 1", "document", "document1",
    "doc1", "new document", "presentation", "presentation1", "workbook",
    "book", "book1", "sheet1", "slide 1", "slide1", "title", "heading",
    "microsoft word", "microsoft excel", "microsoft powerpoint",
    "powerpoint presentation", "word document", "no title", "none", "temp",
    "draft", "scan", "scanned document", "image", "photo", "screenshot",
}

_KNOWN_HEADINGS = {
    "abstract", "keywords", "keyword", "introduction", "literature review",
    "related work", "background", "methodology", "methods", "materials and methods",
    "experimental setup", "design", "design considerations", "results",
    "simulation results", "discussion", "conclusion", "conclusions",
    "cost analysis", "future work", "references", "bibliography", "appendix",
    "acknowledgments", "acknowledgements", "acknowledgement",
    "compliance with ethical standards", "disclosure of conflict of interest",
    "conflict of interest", "author contributions", "data availability",
    "funding", "nomenclature", "overview", "summary", "engineering specifications",
}

_JOURNAL_LABELS = {
    "research article", "original article", "review article", "short communication",
    "technical note", "case study", "conference paper", "full length article",
}

_MONTHS = (
    "january", "february", "march", "april", "may", "june", "july", "august",
    "september", "october", "november", "december",
    "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct",
    "nov", "dec",
)
_MONTH_ALT = "|".join(_MONTHS)

_DATE_RE = re.compile(r"^\s*\d{1,4}[-/.]\d{1,2}[-/.]\d{1,4}\s*$")
_MONTH_YEAR_RE = re.compile(
    rf"^\s*(?:{_MONTH_ALT})(?:\s*[-/.,]\s*|\s+)(?:19|20)?\d{{2}}\s*$",
    re.IGNORECASE,
)
_YEAR_MONTH_RE = re.compile(
    rf"^\s*(?:19|20)?\d{{2}}(?:\s*[-/.,]\s*|\s+)(?:{_MONTH_ALT})\s*$",
    re.IGNORECASE,
)
_PAGE_NO_RE = re.compile(
    r"^\s*(page\s*)?[-#]?\s*\d+\s*(of\s*\d+)?\s*$",
    re.IGNORECASE,
)
_URL_EMAIL_RE = re.compile(
    r"^\s*(https?://\S+|www\.\S+|\S+@\S+\.\S+)\s*$",
    re.IGNORECASE,
)
_TOOL_PREFIX_RE = re.compile(
    r"^(microsoft (word|excel|powerpoint)|libreoffice \w+|openoffice \w+)\s*[-:]\s*",
    re.IGNORECASE,
)
_TOC_LEADER_RE = re.compile(r"\s*[\.·•…]{2,}\s*\d+\s*$")
_WORD_RE = re.compile(r"\b\w+\b", re.UNICODE)
_NUM_PREFIX_RE = re.compile(
    r"^(?:(?:chapter|section|part|article|appendix|annex)\s+)?"
    r"(?:[0-9]+(?:\.[0-9]+)*|[IVXLCDM]+)[.)]?\s+",
    re.IGNORECASE,
)
_CAPTION_RE = re.compile(
    r"^(?:figure|fig\.?|table|tab\.?|equation|eq\.?|plate|scheme)\s*"
    r"[a-z]?\d+[a-z]?(?:\.\d+)?\b",
    re.IGNORECASE,
)
_MEASUREMENT_RE = re.compile(
    r"^\d+(?:\.\d+)?\s*"
    r"(?:hp|kwh?|kw|mw|w|v|a|mm|cm|m|kg|g|lb|n|kn|mpa|psi|rpm|°c|c|%|l|ml)\b",
    re.IGNORECASE,
)
_NUMBERED_HEADING_RE = re.compile(
    r"^(?:"
    r"(?:chapter|section|part|article|appendix|annex|clause)\s+"
    r"(?:\d+(?:\.\d+)*|[IVXLCDM]+)\.?\s+(.+)"
    r"|(\d+\.\d+(?:\.\d+){0,4}|\d+)\.\s+(.+)"
    r"|(\d+\.\d+(?:\.\d+){0,4})\s+(.+)"
    r"|([IVXLCDM]+)\.\s+(.+)"
    r")$",
    re.IGNORECASE,
)
_METADATA_LABEL_RE = re.compile(
    r"^(keywords?|corresponding author|doi|received|accepted|published|"
    r"available online|e-?mail)\s*:",
    re.IGNORECASE,
)
_AUTHOR_RE = re.compile(
    r"\b(prof\.|dr\.|ph\.?d|university|college of|corresponding author|"
    r"e-?mail|copyright\s*©)\b",
    re.IGNORECASE,
)
_PAREN_LABEL_RE = re.compile(r"^\([^)]{3,40}\)$")
_YEAR_TAIL_RE = re.compile(r",?\s*(?:19|20)\d{2}$")


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def strip_toc_leader(text: str) -> str:
    return _TOC_LEADER_RE.sub("", text).strip()


def canonical_header_key(text: str) -> str:
    t = normalize(strip_toc_leader(text)).lower()
    t = t.strip(" :.-—–")
    t = _NUM_PREFIX_RE.sub("", t)
    t = re.sub(r"[\s—–\-|:·•]+", " ", t).strip()
    return t


def running_header_key(text: str) -> str:
    t = normalize(text).lower()
    t = re.sub(r"[()\[\]]", "", t)
    t = _YEAR_TAIL_RE.sub("", t)
    t = re.sub(r"\s+", " ", t).strip(" ,.-")
    return t


def is_date_like(text: str) -> bool:
    t = normalize(text)
    if not t:
        return False
    if _DATE_RE.match(t) or _MONTH_YEAR_RE.match(t) or _YEAR_MONTH_RE.match(t):
        return True
    if re.fullmatch(r"(?:19|20)\d{2}", t):
        return True
    if re.fullmatch(r"(?:Q[1-4]|FY|H[12])[\s\-/]?(?:19|20)?\d{2}", t, re.IGNORECASE):
        return True
    return False


def is_weak_title(text: str) -> bool:
    t = normalize(text)
    if not t or is_date_like(t):
        return True
    letters = [c for c in t if c.isalpha()]
    if len(letters) < 6:
        return True
    words = [w for w in re.split(r"[\s/_\-—–|:·•]+", t) if w]
    alpha_words = [w for w in words if re.search(r"[A-Za-z]", w)]
    if len(alpha_words) <= 1 and len(letters) < 18:
        return True
    return False


def clean_candidate(text: Optional[str], *, allow_weak: bool = False, max_len: int = MAX_TITLE_LEN) -> Optional[str]:
    if not text:
        return None
    t = normalize(strip_toc_leader(text))
    t = _TOOL_PREFIX_RE.sub("", t)
    t = t.strip(" \t-_:;,|·•—–")
    if not t:
        return None
    t = re.sub(
        r"\.(docx?|xlsx?|pptx?|pdf|odt|ods|odp|rtf|html?|epub|txt|md"
        r"|csv|tsv|json|xml|log|png|jpe?g|webp|bmp|tiff?|gif)$",
        "",
        t,
        flags=re.IGNORECASE,
    ).strip()
    if len(t) < 3 or len(t) > max_len:
        return None
    if t.lower() in _GENERIC_TITLES:
        return None
    if not re.search(r"[^\W\d_]", t, re.UNICODE):
        return None
    if _DATE_RE.match(t) or _PAGE_NO_RE.match(t) or _URL_EMAIL_RE.match(t):
        return None
    if is_date_like(t):
        return None
    if not allow_weak and is_weak_title(t):
        return None
    return t


def _merge_title_parts(primary: str, secondary: str) -> str:
    primary = normalize(primary).rstrip(" —–-|:·•")
    secondary = normalize(secondary).lstrip(" —–-|:·•")
    if not secondary:
        return primary
    if not primary:
        return secondary
    if secondary.lower() in primary.lower():
        return primary
    return f"{primary} — {secondary}"


def _word_count(text: str) -> int:
    return len(_WORD_RE.findall(text or ""))


def body_font_size(lines: Sequence[Line]) -> float:
    weights: Counter[float] = Counter()
    for ln in lines:
        if ln.size > 0 and ln.text:
            weights[round(ln.size, 1)] += max(len(ln.text), 1)
    if not weights:
        return 11.0
    return weights.most_common(1)[0][0]


def looks_like_sentence(text: str) -> bool:
    t = normalize(text)
    if not t:
        return True
    if t[:1].islower():
        return True
    if t.endswith(",") and _word_count(t) >= 6:
        return True
    if "$" in t or "@" in t or "http://" in t.lower() or "https://" in t.lower():
        return True
    if re.search(r"[a-z]{2}\.\s+[A-Z]", t):
        return True
    if _word_count(t) > 14:
        return True
    return False


def is_caption(text: str) -> bool:
    return bool(_CAPTION_RE.match(normalize(text)))


def is_journal_label(text: str) -> bool:
    key = running_header_key(text)
    if key in _JOURNAL_LABELS:
        return True
    if _PAREN_LABEL_RE.match(normalize(text)):
        inner = running_header_key(text)
        if inner in _JOURNAL_LABELS or inner.isupper():
            return True
    return False


def is_author_or_footnote(ln: Line) -> bool:
    text = normalize(ln.text)
    if is_numbered_heading(text) or is_known_heading(text):
        return False
    if _AUTHOR_RE.search(text):
        return True
    footer = ln.page_height > 0 and ln.y0 >= ln.page_height * 0.88
    if footer and ("corresponding" in text.lower() or "@" in text or "copyright" in text.lower()):
        return True
    return False


def is_noise_line(ln: Line, running: set[str]) -> bool:
    text = normalize(ln.text)
    if not text:
        return True
    key = running_header_key(text)
    if key in running or canonical_header_key(text) in running:
        return True
    if _PAGE_NO_RE.match(text) or is_date_like(text):
        return True
    if is_caption(text) or is_journal_label(text) or is_author_or_footnote(ln):
        return True
    if _MEASUREMENT_RE.match(text) and looks_like_sentence(text):
        return True
    if _METADATA_LABEL_RE.match(text) and canonical_header_key(text.split(":", 1)[0]) != "keywords":
        return True
    return False


def is_known_heading(text: str) -> bool:
    return canonical_header_key(text) in _KNOWN_HEADINGS


def is_numbered_heading(text: str) -> bool:
    t = normalize(text)
    if _MEASUREMENT_RE.match(t):
        return False
    match = _NUMBERED_HEADING_RE.match(t)
    if not match:
        return False
    groups = [g.strip() for g in match.groups() if g]
    rest = groups[-1] if groups else ""
    if not rest or _word_count(rest) > 14:
        return False
    if looks_like_sentence(rest) or looks_like_sentence(t):
        return False
    if not re.match(r"^[A-Z(]", rest):
        return False
    letters = sum(1 for c in rest if c.isalpha())
    if letters < 4:
        return False
    return True


def is_subhead_label(ln: Line, body: float) -> bool:
    """Bold/title-case definition labels such as 'Portability and mobility:'."""
    text = normalize(ln.text).rstrip(":")
    words = _word_count(text)
    if words < 1 or words > 8:
        return False
    if looks_like_sentence(text) or is_caption(text):
        return False
    if not ln.bold:
        return False
    ratio = (ln.size / body) if body > 0 else 1.0
    if ratio < 0.96:
        return False
    letters = [c for c in text if c.isalpha()]
    if len(letters) < 8:
        return False
    titlecase = all(w[0].isupper() for w in text.split() if w and w[0].isalpha())
    colon = normalize(ln.text).endswith(":")
    return colon or titlecase


def _running_header_keys(pages: Sequence[PageExtract]) -> set[str]:
    if len(pages) < 2:
        return set()
    votes: Counter[str] = Counter()
    for page in pages:
        h = page.height or 0.0
        if h <= 0:
            continue
        seen: set[str] = set()
        for ln in page.lines:
            top_band = ln.y0 <= h * 0.16
            bot_band = ln.y1 >= h * 0.88
            if not (top_band or bot_band):
                continue
            key = running_header_key(ln.text)
            if len(key) < 4 or key in seen:
                continue
            seen.add(key)
            votes[key] += 1
    threshold = 2 if len(pages) <= 3 else max(3, int(len(pages) * 0.35))
    return {k for k, n in votes.items() if n >= threshold}


def _score_title_line(text: str, *, ratio: float, top: float, page_h: float, bold: bool, centered: bool) -> float:
    if is_journal_label(text) or is_caption(text):
        return -1.0
    letters = [c for c in text if c.isalpha()]
    caps_frac = (sum(c.isupper() for c in letters) / len(letters)) if letters else 0.0
    words = [w for w in text.split() if any(c.isalpha() for c in w)]
    titlecase_frac = (sum(w[0].isupper() for w in words) / len(words)) if words else 0.0

    if ratio < 1.12 and caps_frac < 0.6 and titlecase_frac < 0.6 and not bold:
        return -1.0

    score = 1.7 * max(0.0, min(ratio, 2.8) - 1.0)
    score += 1.05 * (1.0 - top / max(page_h * VISUAL_TOP_FRACTION, 1.0))
    if bold:
        score += 0.35
    if centered:
        score += 0.25
    if caps_frac >= 0.6 and len(letters) >= 4:
        score += 0.35
    if len(words) >= 2:
        score += 0.55
    if len(words) >= 3:
        score += 0.30
    if 12 <= len(text) <= 90:
        score += 0.30
    if len(text) <= 8:
        score -= 0.60
    if is_date_like(text) or is_weak_title(text):
        score -= 1.50
    if text.rstrip().endswith((".", "!", "?", ";", ",")):
        score -= 0.45
    if len(words) > 14:
        score -= 0.55
    if looks_like_sentence(text):
        score -= 1.8
    return score


def _is_centered(ln: Line) -> bool:
    if ln.page_width <= 0:
        return False
    mid = (ln.x0 + ln.x1) / 2.0
    return abs(mid - ln.page_width / 2.0) <= ln.page_width * 0.18


def join_title_lines(lines: Sequence[Line]) -> str:
    """Join wrapped title lines using the original PDF words only."""
    chunks: List[str] = []
    for ln in lines:
        piece = normalize(ln.text)
        if not piece:
            continue
        if chunks:
            prev = chunks[-1]
            if prev.endswith("-") and re.search(r"[A-Za-z]$", prev[:-1]) and piece[:1].isalpha():
                chunks[-1] = prev[:-1] + piece
                continue
        chunks.append(piece)
    text = normalize(" ".join(chunks)).strip(" \t-_:;")
    if len(text) > MAX_TITLE_LEN:
        text = text[:MAX_TITLE_LEN].rsplit(" ", 1)[0]
    return text


def _is_title_stop(ln: Line) -> bool:
    text = normalize(ln.text)
    if not text:
        return True
    if is_known_heading(text) or is_numbered_heading(text) or is_caption(text):
        return True
    if is_journal_label(text):
        return True
    if _AUTHOR_RE.search(text):
        return True
    if text.count(",") >= 3 and _word_count(text) >= 6:
        return True
    return False


def _same_wrap(prev: Line, nxt: Line, title_size: float) -> bool:
    if _is_title_stop(nxt):
        return False
    gap = nxt.y0 - prev.y1
    if gap > max(title_size * 0.95, 12):
        return False
    if nxt.size + 0.4 < title_size * 0.88:
        return False
    prev_mid = (prev.x0 + prev.x1) / 2.0
    nxt_mid = (nxt.x0 + nxt.x1) / 2.0
    aligned = abs(nxt.x0 - prev.x0) <= 36 or abs(nxt_mid - prev_mid) <= 48
    return aligned


def extract_title_band(
    page: PageExtract,
    body_size: float,
    running: Optional[set[str]] = None,
) -> TitleBand:
    """Cluster the largest-font wrapped lines at the top of the page into one title."""
    running = running or set()
    page_h = page.height or 0.0
    if not page.lines or page_h <= 0:
        return TitleBand()

    ordered = sorted(page.lines, key=lambda ln: (ln.y0, ln.x0))
    top = [ln for ln in ordered if ln.y0 <= page_h * VISUAL_TOP_FRACTION]
    usable = [
        ln for ln in top
        if not is_noise_line(ln, running) and not _is_title_stop(ln)
    ]
    if not usable:
        return TitleBand(context=_title_context(ordered))

    max_size = max(ln.size for ln in usable)
    if body_size > 0 and max_size < body_size * 1.08:
        seeds = usable
    else:
        seeds = [ln for ln in usable if ln.size >= max_size * 0.92]
    if not seeds:
        seeds = usable
    seed = min(seeds, key=lambda ln: ln.y0)
    title_size = seed.size

    try:
        idx = ordered.index(seed)
    except ValueError:
        return TitleBand(context=_title_context(ordered))

    start = idx
    while start > 0 and _same_wrap(ordered[start - 1], ordered[start], title_size):
        if is_noise_line(ordered[start - 1], running):
            break
        start -= 1
    end = idx
    while end + 1 < len(ordered) and (end - start + 1) < TITLE_BAND_MAX_LINES:
        nxt = ordered[end + 1]
        if is_noise_line(nxt, running) or not _same_wrap(ordered[end], nxt, title_size):
            break
        end += 1

    band_lines = ordered[start : end + 1]
    return TitleBand(
        lines=band_lines,
        text=join_title_lines(band_lines),
        context=_title_context(ordered, skip=set(id(ln) for ln in band_lines)),
    )


def _title_context(ordered: Sequence[Line], skip: Optional[set[int]] = None) -> str:
    skip = skip or set()
    parts: List[str] = []
    for ln in ordered:
        if id(ln) in skip:
            continue
        text = normalize(ln.text)
        if not text:
            continue
        parts.append(text)
        if len(parts) >= 8:
            break
    return "\n".join(parts)


def visual_title(page: PageExtract, body_size: float, running: Optional[set[str]] = None) -> Optional[str]:
    if not page.lines:
        return None
    page_h = page.height or 0.0
    if page_h <= 0:
        return None
    running = running or set()
    best: Optional[Tuple[float, float, float, str]] = None
    for i, ln in enumerate(page.lines):
        if ln.y0 > page_h * VISUAL_TOP_FRACTION:
            continue
        if is_noise_line(ln, running):
            continue
        text = clean_candidate(ln.text, allow_weak=True)
        if text is None:
            continue
        ratio = (ln.size / body_size) if body_size > 0 else 1.0
        score = _score_title_line(
            text,
            ratio=ratio,
            top=ln.y0,
            page_h=page_h,
            bold=ln.bold,
            centered=_is_centered(ln),
        )
        if score < VISUAL_MIN_SCORE:
            continue
        merged = text
        if i + 1 < len(page.lines):
            nxt = page.lines[i + 1]
            gap = nxt.y0 - ln.y1
            if gap <= max(ln.size, 1.0) * MERGE_GAP_RATIO and nxt.y0 <= page_h * VISUAL_TOP_FRACTION:
                secondary = normalize(nxt.text)
                if secondary and (is_date_like(secondary) or len(secondary.split()) <= 4) and not looks_like_sentence(secondary):
                    merged = clean_candidate(_merge_title_parts(text, secondary), allow_weak=True) or text
        merged = clean_candidate(merged) or clean_candidate(text)
        if merged is None:
            continue
        cand = (score, -ln.y0, -ln.x0, merged)
        if best is None or cand[:3] > best[:3]:
            best = cand
    return best[3] if best else None


def first_content_line(pages: Sequence[PageExtract], running: Optional[set[str]] = None) -> Optional[str]:
    running = running or set()
    for page in pages:
        for ln in page.lines:
            if is_noise_line(ln, running) or looks_like_sentence(ln.text):
                continue
            t = clean_candidate(ln.text)
            if t:
                return t
        for raw in page.text.split("\n"):
            t = clean_candidate(raw)
            if t and not looks_like_sentence(t):
                return t
    return None


def is_duplicate_header(
    text: str,
    seen_keys: Sequence[str],
    *,
    title_key: Optional[str] = None,
) -> bool:
    key = canonical_header_key(text)
    if not key:
        return True
    if title_key:
        if key == title_key:
            return True
        padded = f" {title_key} "
        if (
            padded.startswith(f" {key} ")
            or padded.endswith(f" {key} ")
            or f" {key} " in padded
        ):
            return True
    for prev in seen_keys:
        if key == prev:
            return True
        longer, shorter = (key, prev) if len(key) > len(prev) else (prev, key)
        if not longer.startswith(shorter + " "):
            continue
        extra = longer[len(shorter):].strip()
        if is_date_like(extra):
            return True
    return False


def filename_title(filename: Optional[str]) -> Optional[str]:
    if not filename:
        return None
    stem = re.sub(r"\.[A-Za-z0-9]{1,5}$", "", filename)
    stem = re.sub(r"[_\-.]+", " ", stem)
    return clean_candidate(stem)


def _select_scan_pages(pages: Sequence[PageExtract]) -> List[PageExtract]:
    selected: List[PageExtract] = []
    for page in pages:
        selected.append(page)
        if _word_count(page.text) >= MIN_SCAN_PAGE_WORDS:
            break
    return selected


def resolve_document_title(
    doc: NativeDocument,
    *,
    filename: Optional[str] = None,
) -> Tuple[str, str, TitleBand]:
    pages = doc.pages
    scan = _select_scan_pages(pages)
    all_lines = [ln for p in pages for ln in p.lines]
    body = body_font_size(all_lines)
    running = _running_header_keys(pages)
    band = TitleBand()
    if scan:
        band = extract_title_band(scan[0], body, running)

    heading_hint = None
    if doc.toc:
        heading_hint = clean_candidate(doc.toc[0][1], allow_weak=True)
        if heading_hint and (is_journal_label(heading_hint) or is_caption(heading_hint)):
            heading_hint = None

    if band.text and not is_weak_title(band.text):
        return band.text, "visual", band
    if band.text:
        return band.text, "visual", band

    if heading_hint and not is_weak_title(heading_hint) and not is_date_like(heading_hint):
        for page in scan:
            visual = visual_title(page, body, running)
            if visual:
                return visual, "visual", band
        return heading_hint, "toc", band

    for page in scan:
        visual = visual_title(page, body, running)
        if visual:
            return visual, "visual", band

    if heading_hint:
        strong = first_content_line(scan or pages, running)
        if strong:
            return strong, "content", band
        cleaned = clean_candidate(heading_hint)
        if cleaned:
            return cleaned, "toc", band

    t = clean_candidate(doc.meta_title)
    if t:
        return t, "metadata", band

    t = first_content_line(pages, running)
    if t:
        return t, "content", band

    t = filename_title(filename)
    if t:
        return t, "filename", band

    return "Untitled document", "none", band


def extract_title(
    doc: NativeDocument,
    *,
    filename: Optional[str] = None,
) -> Tuple[str, str]:
    text, source, _band = resolve_document_title(doc, filename=filename)
    return text, source


def _heading_level(size: float, body: float, text: str, toc_level: Optional[int] = None) -> int:
    if toc_level is not None:
        return max(1, min(int(toc_level), 6))
    key = canonical_header_key(text)
    numbered = is_numbered_heading(text)
    dots = text.split()[0].count(".") if numbered else 0
    if numbered and re.match(r"^\d+\.\d+", normalize(text)):
        return min(2 + dots, 5)
    if body <= 0:
        return 1
    ratio = size / body
    if ratio >= 1.45 or key in {"abstract", "introduction", "references"}:
        return 1
    if numbered or ratio >= 1.18:
        return 2
    if ratio >= 1.08 or is_known_heading(text):
        return 3
    return 4


def heading_text_from_line(ln: Line) -> Optional[str]:
    raw = normalize(ln.text)
    meta = _METADATA_LABEL_RE.match(raw)
    if meta and canonical_header_key(meta.group(1)) == "keywords":
        return "Keywords"
    text = raw.rstrip(":")
    cleaned = clean_candidate(text, allow_weak=True, max_len=MAX_HEADER_LEN)
    return cleaned


def _is_heading_line(ln: Line, body: float, running: set[str]) -> bool:
    if is_noise_line(ln, running):
        return False
    text = normalize(ln.text)
    if not text or len(text) > MAX_HEADER_LEN:
        return False
    ratio = (ln.size / body) if body > 0 else 1.0

    if is_numbered_heading(text):
        return True
    if is_known_heading(text) and not looks_like_sentence(text):
        return True
    if _METADATA_LABEL_RE.match(text) and canonical_header_key(text.split(":", 1)[0]) == "keywords":
        return True
    if looks_like_sentence(text):
        return False
    if is_subhead_label(ln, body):
        return True
    words = _word_count(text)
    if words <= 10 and ratio >= 1.08 and not looks_like_sentence(text):
        letters = [c for c in text if c.isalpha()]
        caps = (sum(c.isupper() for c in letters) / len(letters)) if letters else 0.0
        titlecase = all(w[0].isupper() for w in text.split() if w and w[0].isalpha())
        if ln.bold or caps >= 0.7 or titlecase:
            return True
    if ln.bold and ratio >= 1.05 and words <= 8 and not looks_like_sentence(text):
        return True
    return False


def _merge_multiline_headers(lines: Sequence[Line], body: float, running: set[str]) -> List[Tuple[Line, str]]:
    items: List[Tuple[Line, str]] = []
    i = 0
    n = len(lines)
    while i < n:
        ln = lines[i]
        if not _is_heading_line(ln, body, running):
            i += 1
            continue
        text = heading_text_from_line(ln) or strip_toc_leader(normalize(ln.text))
        complete = is_numbered_heading(ln.text) or is_known_heading(ln.text)
        j = i + 1
        while (not complete) and j < n:
            nxt = lines[j]
            if nxt.page != ln.page:
                break
            if abs(nxt.size - ln.size) > 0.6:
                break
            gap = nxt.y0 - ln.y1
            if gap > max(ln.size, 1.0) * 1.15:
                break
            if not _is_heading_line(nxt, body, running):
                break
            extra = heading_text_from_line(nxt) or strip_toc_leader(normalize(nxt.text))
            if extra.lower() in text.lower():
                j += 1
                continue
            joined = f"{text} {extra}".strip()
            if len(joined) > MAX_HEADER_LEN:
                break
            text = joined
            j += 1
        cleaned = clean_candidate(text, allow_weak=True, max_len=MAX_HEADER_LEN)
        if cleaned and not looks_like_sentence(cleaned) and not is_caption(cleaned):
            items.append((ln, cleaned))
        i = max(j, i + 1)
    return items


def list_headers(
    doc: NativeDocument,
    *,
    title: Optional[str] = None,
) -> List[dict]:
    all_lines = [ln for p in doc.pages for ln in p.lines]
    body = body_font_size(all_lines)
    running = _running_header_keys(doc.pages)
    found = _merge_multiline_headers(all_lines, body, running)

    seen_keys: List[str] = []
    headers: List[dict] = []
    title_key = canonical_header_key(title) if title else None

    def _add(text: str, page: int, level: int, source: str, size: float = 0.0) -> None:
        if source == "title":
            cleaned = normalize(text)
        else:
            cleaned = clean_candidate(text, allow_weak=True, max_len=MAX_HEADER_LEN)
        if not cleaned:
            return
        if source != "title" and (is_caption(cleaned) or is_journal_label(cleaned)):
            return
        if is_duplicate_header(cleaned, seen_keys, title_key=title_key if source != "title" else None):
            return
        seen_keys.append(canonical_header_key(cleaned))
        headers.append({
            "text": cleaned,
            "page": page,
            "level": level,
            "source": source,
            "size": size,
        })

    if title:
        title_page = 1
        title_size = 0.0
        for ln, text in found:
            if canonical_header_key(text) == canonical_header_key(title) or normalize(text).lower() == normalize(title).lower():
                title_page = ln.page
                title_size = ln.size
                break
        _add(title, title_page, 1, "title", title_size)

    for ln, text in found:
        _add(text, ln.page, _heading_level(ln.size, body, text), "font", ln.size)

    for level, toc_title, page in doc.toc:
        cleaned = clean_candidate(toc_title, allow_weak=True, max_len=MAX_HEADER_LEN)
        if not cleaned or is_duplicate_header(cleaned, seen_keys, title_key=title_key):
            continue
        if is_caption(cleaned) or is_journal_label(cleaned):
            continue
        seen_keys.append(canonical_header_key(cleaned))
        page_no = max(int(page), 1)
        entry = {
            "text": cleaned,
            "page": page_no,
            "level": _heading_level(0, body, cleaned, toc_level=level),
            "source": "toc",
            "size": 0.0,
        }
        insert_at = next(
            (i for i, h in enumerate(headers) if h["page"] > page_no and h["source"] != "title"),
            len(headers),
        )
        if insert_at == 0 and headers and headers[0]["source"] == "title":
            insert_at = 1
        headers.insert(insert_at, entry)

    return [
        {
            "index": i,
            "text": item["text"],
            "page": item["page"],
            "level": item["level"],
            "source": item["source"],
        }
        for i, item in enumerate(headers, start=1)
    ]


def headers_listed(headers: Iterable[dict]) -> List[str]:
    return [f"{h['index']}. {h['text']}" for h in headers]
