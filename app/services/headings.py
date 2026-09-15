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
    candidates: List[str] = field(default_factory=list)
    uncertain: bool = False

MAX_TITLE_LEN = 240
MAX_HEADER_LEN = 160
VISUAL_TOP_FRACTION = 0.52
VISUAL_MIN_SCORE = 1.15
MIN_SCAN_PAGE_WORDS = 80
MERGE_GAP_RATIO = 2.2
TITLE_BAND_MAX_LINES = 5
# Wrapped title lines share a font size; cover subtitles and bylines are smaller.
WRAP_SIZE_RATIO = 0.95
SEED_SIZE_RATIO = 0.98
_WRAP_TAIL_WORDS = {
    "and", "or", "of", "for", "the", "a", "an", "to", "with", "in", "on",
    "by", "from", "into", "using", "via", "at", "as", "over", "under",
}
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_GENERIC_TITLES = {
    "untitled", "untitled document", "untitled 1", "document", "document1",
    "doc1", "new document", "presentation", "presentation1", "workbook",
    "book", "book1", "sheet1", "slide 1", "slide1", "title", "heading",
    "microsoft word", "microsoft excel", "microsoft powerpoint",
    "powerpoint presentation", "word document", "no title", "none", "temp",
    "draft", "scan", "scanned document", "image", "photo", "screenshot",
}

_WEB_CHROME_TOKENS = frozenset({
    "openinapp", "open in app", "signup", "sign up", "signin", "sign in",
    "subscribe", "follow", "search", "write", "menu", "log in", "login",
    "get started", "read more", "share", "comment", "listen",
})
MEDIUM_PRINT_CHROME_LINES = 4 
_VIEW_TOC_LINK_RE = re.compile(r"view\s+table\s+of\s+contents\s*:\s*\S+", re.IGNORECASE)
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
    "manuscript info", "manuscript history", "article info", "article history",
}

_JOURNAL_LABELS = {
    "research article", "original article", "review article", "short communication",
    "technical note", "case study", "conference paper", "full length article",
    "research paper", "accepted manuscript", "conference series", "paper open access",
    "editorial board", "aims and scope", "table of contents", "you may also like",
    "original research paper", "original research article", "original research",
    "review paper", "research note", "regular paper", "regular article",
    "invited review", "technical paper", "student paper", "open access",
    "journal pre-proof", "journal preproof", "pre-proof", "preproof",
    "uncorrected proof", "author accepted manuscript", "in press",
    "a scitechnol journal",
}
_JOURNAL_LABEL_RE = re.compile(
    r"^(?:an?\s+)?(?:original|invited|regular|full[\s-]?length|short|technical|student)?\s*"
    r"(?:research|review|scientific|conference)?\s*"
    r"(?:article|paper|communication|note|letter)$",
    re.IGNORECASE,
)
_NUMBERED_INITIAL_AUTHOR_RE = re.compile(r"\b\d{1,2}\s?[A-Z]\.")
_MONTHS = (
    "january", "february", "march", "april", "may", "june", "july", "august",
    "september", "october", "november", "december",
    "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct",
    "nov", "dec",
)
_MIN_READ_RE = re.compile(r"\d+\s*min\s*read", re.IGNORECASE)
_TRAILING_DATE_RE = re.compile(
    r"(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)\w*\s+\d{1,2}\s*,\s*\d{4}",
    re.IGNORECASE,
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
MAX_FRONT_MATTER_PAGES = 6  
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
    r"available online(?:\s+at)?|e-?mail|pii|to appear in|received date|"
    r"revised date|accepted date|authors?|reference|references|journal|"
    r"article history|cite this article|short title|running title)\s*:",
    re.IGNORECASE,
)
_LABELED_TITLE_RE = re.compile(
    r"^\s*(?:project\s+)?tit?tle\s*:\s*(.+)$",
    re.IGNORECASE,
)
_AUTHOR_RE = re.compile(
    r"\b(?:prof|dr|ph\.?d|engr|university|college of|corresponding author|"
    r"e-?mail|copyright|internal editor|editor-in-chief)\b",
    re.IGNORECASE,
)
_AUTHOR_MARK_RE = re.compile(r"\*\s*\d+\b")
_NUMBERED_PERSON_RE = re.compile(r"(?:^|\s)\d+[A-Z][a-z]{2,}")
_NAME_NUM_COMMA_RE = re.compile(
    r"[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ.'\-]*\s*[0-9¹²³⁴⁵⁶⁷⁸⁹*∗†‡]+\s*,\s*[A-ZÀ-ÿ]"
)
_JOURNAL_BANNER_RE = re.compile(
    r"^(?:"
    r"(?:the\s+)?(?:(?:international|iosr|open access|european)\s+)?"
    r"(?:research\s+)?journal(?:s)?\s+of\b"
    r"|materials today\b"
    r"|.+\bdergisi\b"
    r"|facta universitatis\b"
    r"|article in press"
    r"|contents lists available"
    r"|journal homepage"
    r")",
    re.IGNORECASE,
)
_TITLE_BLOCK_STOP_RE = re.compile(
    r"^(?:"
    r"article\s*info|article\s*history|abstract|keywords|"
    r"corresponding author|received|accepted|doi\s*:|"
    r"by\s*$|udc\b|©|copyright\b"
    r")",
    re.IGNORECASE,
)
MAX_CHROME_BLOCK_LINES = 8 
_COVER_CHROME_RE = re.compile(
    r"^(?:"
    r"a\s+(?:project|thesis|seminar|dissertation|technical|internship)\s+report\b.*"
    r"|a\s+(?:minor|major|mini)\s+project\s+report\b.*"
    r"|a\s+(?:thesis|dissertation|project)\s+submitted\b.*"
    r"|submitted (?:by|in|to)\b.*"
    r"|in partial fulfil?lment\b.*"
    r"|(?:bachelors?|masters?|doctor)\s+of\s+\w+.*"
    r"|(?:b\.?tech|m\.?tech|b\.?e\.?|m\.?e\.?|b\.?sc|m\.?sc|ph\.?d)\s+(?:in|degree)\b.*"
    r"|award of the degree\b.*"
    r"|bachelors? of engineering\b.*"
    r"|department of .+"
    r"|associate professor\b.*"
    r"|under the guidance of\b.*"
    r"|carried out at\b.*"
    r"|certificate"
    r"|declaration"
    r"|to cite this article\b.*"
    r"|view the article online\b.*"
    r"|paper\s*[•·]?\s*open access"
    r"|you may also like"
    r"|please cite this article\b.*"
    r")$",
    re.IGNORECASE,
)

_LETTER_AFFIL_AUTHOR_RE = re.compile(
    r"[A-Z][a-z]+(?:-[A-Z][a-z]+)?\s?[a-z]\s*,\s*(?:[∗*†‡]|[A-Z])"
)
# Elsevier "Long a,b" / "Wang a, Chuang-Long" affiliation bylines.
_ELSEVIER_AFFIL_RE = re.compile(
    r"\b[A-Z][A-Za-z'\-]+\s+[a-z](?:\s*,\s*[a-z])+\b"
    r"|^[A-Z][A-Za-z'\-]+\s+[a-z]\s*,\s*[A-Z]"
)
_ADDRESS_RE = re.compile(
    r"(?:\b\d{6}\b"
    r"|[A-Za-z]+,\s+[A-Za-z]+\s+\d{5}(?:-\d{4})?\b"
    r"|,\s*(?:belagavi|bengaluru|bangalore|chennai|mumbai|delhi)\b"
    r"|\b(?:layout|campus|nagar)\b)",
    re.IGNORECASE,
)
_OCR_NOISE_CHARS = frozenset("|/\\_~^`={}[]<>•●■◆")
_CITATION_LINE_RE = re.compile(
    r"(?:\bvol(?:ume)?\.?\s*\d+"
    r"|\bno\.?\s*\d+\s*,"
    r"|\bissue\s*[-:]?\s*\d+"
    r"|\bpp\.?\s*\d+\s*[-–]\s*\d+"
    r"|\b(?:19|20)\d{2}\s*,\s*\d+\s*\(\s*\d+\s*\)"
    r"|\b\d{1,3}\s*\(\s*\d{1,3}\s*\)\s*\(?\s*(?:19|20)\d{2}"
    r"|\bissn\b|\be-?issn\b|\bp-issn\b|\bdoi\b|\bimpact factor\b)",
    re.IGNORECASE,
)
_ALL_PARENS_RE = re.compile(r"^\(.*\)$", re.DOTALL)
_ABBREV_TAIL_RE = re.compile(
    r"(?:\b[A-Z]|\b(?:No|Nos|Inc|Ltd|Co|Corp|Dept|Univ|Fig|Eq|Vol|etc|al))\.$"
)
_PRINT_HEADER_RE = re.compile(
    r"^\d{1,2}/\d{1,2}/\d{2,4},?\s+\d{1,2}:\d{2}\s*[AP]M\b", re.IGNORECASE
)

_AUTHOR_RE = re.compile(
    r"\b(?:prof|dr|ph\.?d|engr|corresponding author|"
    r"e-?mail|copyright|internal editor|editor-in-chief)\b",
    re.IGNORECASE,
)

_INSTITUTION_RE = re.compile(r"\b(?:university|college of)\b", re.IGNORECASE)

_PRINT_HEADER_TITLE_RE = re.compile(
    r"^\d{1,2}/\d{1,2}/\d{2,4},?\s+\d{1,2}:\d{2}\s*[AP]M\s*\|\s*(.+?)\s*\|",
    re.IGNORECASE,
)

_QUOTED_TITLE_RE = re.compile(r"[“\"'«]\s*([^“\"'»]{8,200}?)\s*[”\"'»]")
_NON_NAME_WORDS = frozenset(
    """
    a an and are as at be by for from in into is of on or over the to under using via with
    without within analysis analyses analytical application applications approach assessment
    automobile automotive based behaviour behavior blade blades body brake casting cfd
    characteristics comparative comparison composite composites compressor computational
    concrete control cooling cutter cutting cycle design designing detection development
    device diesel drive dynamic dynamics effect effects efficiency energy engine engineering
    enhancement evaluation experimental fabrication finite flow fluid fluids friction fuel
    gear generation harvesting heat helical hybrid hydraulic impact improvement industrial
    investigation machine machines machining manufacturing material materials mechanical
    mechanism method methods model modeling modelling nano nanofluid nanofluids numerical
    optimization optimisation performance plant power pressure process processing production
    project pump quality radiator refrigeration reliability renewable research review
    robot rotor simulation solar steel strength structural structure study surface system
    systems technique techniques technology temperature test testing thermal transfer
    treatment tube tubes turbine vehicle velocity vibration water wear weld welding
    wind work
    """.split()
)
_FRONT_MATTER_MARKERS = (
    "editorial board", "table of contents", "aims and scope",
    "a journal produced by", "guide for authors",
    "internal editor", "editor-in-chief", "cover page design",
)
_COMMON_ENGLISH_WORDS = frozenset({
    "the", "of", "and", "a", "to", "in", "is", "that", "for", "on",
    "with", "as", "by", "this", "are", "be", "or", "an", "it", "at",
    "from", "which", "was", "were", "has", "have", "can", "not", "its",
})
ARTIFACT_FONT_SIZE = 40.0
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
def _is_chrome_line(text: str) -> bool:
    """Any of the known non-title UI patterns — used only for the
    contiguous chrome-block skip right after a print-header line."""
    return (
        _is_web_chrome_line(text)
        or _is_tag_pill_line(text)
        or _is_byline_meta_line(text)
        or _is_noisy_ocr_title(text)
    )
def _strip_print_chrome_block(lines: list[str]) -> list[str]:
    for i, line in enumerate(lines):
        if not _is_print_header_line(line):
            continue
        match = _PRINT_HEADER_TITLE_RE.match(line)
        j = i + 1
        limit = min(len(lines), i + 1 + MAX_CHROME_BLOCK_LINES)
        while j < limit and _is_chrome_line(lines[j]):
            j += 1
        del lines[i:j]
        if match:
            candidate = normalize(match.group(1))
            if candidate:
                lines.insert(i, candidate)  # recovered real title, re-inserted clean
        break
    return lines

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

def _has_plausible_english_words(text: str, min_words: int = 6) -> bool:
    words = re.findall(r"[a-zA-Z]+", text.lower())
    if len(words) < min_words:
        return True
    hits = sum(1 for w in words if w in _COMMON_ENGLISH_WORDS)
    return (hits / len(words)) >= 0.10  # short strings need a higher hit-rate to trust

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

def _is_byline_meta_line(text: str) -> bool:
    """Medium/Substack byline row: author handle + 'N min read' + publish
    date, e.g. 'Jane Doe · 3 min read · Dec 24, 2025'. Never article content."""
    t = (text or "").strip()
    if not t:
        return False
    if _MIN_READ_RE.search(t):
        return True
    if _TRAILING_DATE_RE.search(t) and len(t.split()) <= 10:
        return True
    return False
    
def is_garbled_text(text: str) -> bool:
    """True when PDF text is a custom-font / CID dump, not readable English."""
    raw = text or ""
    if not raw.strip():
        return False
    letters = [c for c in raw if c.isalpha()]
    if letters:
        latin = sum(1 for c in letters if "A" <= c <= "Z" or "a" <= c <= "z")
        if latin / len(letters) < 0.55:
            return True
    bad = sum(1 for c in raw if ord(c) < 32 or 0xE000 <= ord(c) <= 0xF8FF)
    if len(raw) >= 6 and bad / len(raw) >= 0.25:
        return True
    # Any embedded control character at all — even one — is a strong signal
    # of a broken font/ToUnicode mapping. Real extracted PDF prose never
    # contains these, so this doesn't need a length or ratio threshold the
    # way the check above does.
    if _CONTROL_CHAR_RE.search(raw):
        return True
    if len(raw) >= 120 and not _has_plausible_english_words(raw):
        return True
    return False
def _is_tag_pill_line(text: str) -> bool:
    """Medium/Substack-style topic-tag row: 'Tag1 + Tag2 + Tag3 +' — the '+'
    is a follow button rendered next to each tag, not prose punctuation."""
    t = (text or "").strip()
    return t.count("+") >= 2

def _is_noisy_ocr_title(text: str) -> bool:
    """Catches short OCR misreads (stray rules, borders, seals, isolated
    glyphs) that is_garbled_text doesn't — this is Latin-script noise from
    a bad crop/scan, not CID/font-encoding garbage."""
    t = (text or "").strip()
    if not t:
        return True

    # Tesseract's classic misreads of vertical rules, table borders,
    # underscores from redaction bars, decorative bullets/logos.
    if any(c in _OCR_NOISE_CHARS for c in t):
        return True

    words = t.split()
    if words:
        stripped = [w.strip(".,)(:;\"'") for w in words]
        # too many single-character "words" — digits, lone letters from a
        # seal/logo, punctuation fragments
        junk_words = sum(1 for w in stripped if len(w) <= 1)
        if junk_words / len(words) > 0.3:
            return True
        # a title made almost entirely of numbers/codes isn't a title —
        # catches stray page numbers, form codes, ISBNs caught in the crop
        digit_words = sum(1 for w in stripped if w and w.isdigit())
        if digit_words / len(words) > 0.4:
            return True
        # real titles have some length variation; repeated near-identical
        # short tokens usually means a repeating logo/watermark got OCR'd
        if len(words) >= 3 and len(set(w.lower() for w in stripped)) == 1:
            return True

    non_space = [c for c in t if not c.isspace()]
    if non_space:
        alpha = sum(1 for c in non_space if c.isalpha())
        if alpha / len(non_space) < 0.75:
            return True
        # excessive punctuation/symbol density relative to letters —
        # common when OCR reads a stamp, border, or table fragment
        punct = sum(1 for c in non_space if not c.isalnum())
        if punct / len(non_space) > 0.35:
            return True

    return False

def _is_web_chrome_line(text: str) -> bool:
    """Website navbar/toolbar text (Medium, Substack, etc.) that survives
    OCR as short, capitalized, plausible-looking 'titles' but is UI chrome,
    not article content."""
    t = normalize(text).lower()
    if not t:
        return False
    compact = re.sub(r"[^a-z]", "", t)
    words = t.split()
    # short line where most/all words are chrome tokens
    if len(words) <= 5:
        hits = sum(1 for token in _WEB_CHROME_TOKENS if token.replace(" ", "") in compact)
        if hits >= 1 and len(words) <= 4:
            return True
    return False

def looks_like_author_line(text: str) -> bool:
    t = normalize(text)
    if not t or is_numbered_heading(t) or is_known_heading(t):
        return False
    if _AUTHOR_MARK_RE.search(t) or _NUMBERED_PERSON_RE.search(t) or _NAME_NUM_COMMA_RE.search(t):
        return True
    if _NUMBERED_INITIAL_AUTHOR_RE.search(t) and _word_count(t) <= 16:
        return True
    if t.lstrip().startswith("*") and _word_count(t) <= 10:
        return True
    if len(_LETTER_AFFIL_AUTHOR_RE.findall(t)) >= 2:
        return True
    if _ELSEVIER_AFFIL_RE.search(t) and _word_count(t) <= 16:
        return True
    if re.match(r"^(?:students?|lecturers?)\b", t, re.IGNORECASE) and _word_count(t) <= 6:
        return True
    if t.count("*") >= 2 or t.count("#") >= 2:
        return True
    if _AUTHOR_RE.search(t):
        return True
    if _INSTITUTION_RE.search(t) and _word_count(t) <= 8:
        return True
    if re.search(r"\bdepartment\b.*\b(?:institute|university|college)\b", t, re.IGNORECASE):
        return True
    if _looks_like_person_name(t):
        return True
    return False


def _looks_like_person_name(text: str) -> bool:
    """Author byline without explicit markers, e.g. 'Amr M. Hassaan'.

    Requires a positive name signal (an initial, an all-caps surname, or a
    comma-separated byline). Title-case noun phrases such as 'Hybrid Nanofluid'
    or 'Automobile Radiator Using Helical Tubes' must not match: truncating a
    title there is far worse than letting an unmarked byline through, since the
    wrap rules reject smaller author type anyway.
    """
    words = [w.strip(",.;") for w in text.replace("&", " ").split() if w.strip(",.;")]
    if not (2 <= len(words) <= 6):
        return False
    if any(w.lower().strip(".") in _NON_NAME_WORDS for w in words):
        return False
    name_tok = re.compile(
        r"^(?:[A-Z][a-z]+|[A-Z]{2,}|[A-Z])(?:[.'\-]?[A-Za-z]+)*\.?$"
    )
    if not all(name_tok.match(w) for w in words):
        return False
    initials = sum(1 for w in words if re.fullmatch(r"[A-Z]\.?", w))
    caps_surname = sum(1 for w in words if len(w) >= 3 and w.isupper())
    mixed_case = any(re.fullmatch(r"[A-Z][a-z]+", w) for w in words)
    if initials:
        return True
    if caps_surname and mixed_case:
        return True
    if text.count(",") >= 1 and len(words) >= 3:
        return True
    return False


def is_cover_chrome(text: str) -> bool:
    t = normalize(text)
    if not t:
        return False
    key = running_header_key(t)
    if key in _JOURNAL_LABELS:
        return True
    if _COVER_CHROME_RE.match(t):
        return True
    if _ADDRESS_RE.search(t) and _word_count(t) <= 10:
        return True
    if re.search(r"\b(technological university|institute of technology)\b", t, re.IGNORECASE):
        return True
    if _JOURNAL_BANNER_RE.match(t) and _word_count(t) <= 16:
        return True
    if re.search(
        r"\b(?:university|institute of engineering|college of engineering)\b",
        t,
        re.IGNORECASE,
    ) and _word_count(t) <= 10:
        topical = re.search(
            r"\b(?:using|towards|via|based|design|system|analysis|effect|investigation)\b",
            t,
            re.IGNORECASE,
        )
        if not topical and (
            t.isupper()
            or re.search(
                r"(?:university|institute|college)\s*$",
                t,
                re.IGNORECASE,
            )
            or re.search(r"\bcollege of engineering\b", t, re.IGNORECASE)
        ):
            return True
    if re.match(r"^proceedings of\b", t, re.IGNORECASE):
        return True
    if "www." in t.lower() and _word_count(t) <= 16:
        return True
    if t.isupper() and t in {
        "TECHNOLOGY", "SCIENCES", "RESEARCH", "LETTERS", "PROCEEDINGS",
        "TRANSACTIONS", "ARCHIVE", "ARCHIVES",
    }:
        return True
    if re.fullmatch(
        r"(?:the\s+)?(?:international\s+journal\s+of\s+)?(?:mechanical\s+)?"
        r"engineering(?:\s+and\s+(?:sciences|technology))?",
        t,
        re.IGNORECASE,
    ):
        return True
    words = t.split()
    if len(words) >= 3 and all(len(w) == 1 and w.isalpha() for w in words):
        return True
    return False


def is_font_artifact(ln: Line) -> bool:
    return ln.size >= ARTIFACT_FONT_SIZE


def strip_title_affixes(text: str) -> str:
    """Drop URL prefixes and trailing author lists from an otherwise-good title."""
    t = normalize(text)
    t = re.sub(r"^(?:https?://\S+|www\.\S+)\s+", "", t, flags=re.IGNORECASE)
    t = re.sub(r"^available online(?:\s+at)?:\s+\S+\s+", "", t, flags=re.IGNORECASE)
    t = re.sub(r"^(?:title|subject)\s*:\s*", "", t, flags=re.IGNORECASE)
    cut = None
    for rx in (_AUTHOR_MARK_RE, _NUMBERED_PERSON_RE, _NAME_NUM_COMMA_RE):
        match = rx.search(t)
        if match and match.start() >= 12:
            start = match.start()
            if cut is None or start < cut:
                cut = start
    if cut is not None:
        t = t[:cut].strip(" ,;:-")
    t = strip_toc_leader(t).strip(" \t-_:;,|·•“”\"'")
    # Running headers often carry the publication year: "... MACHINE 2015".
    stripped = _YEAR_TAIL_RE.sub("", t).strip(" ,;:-")
    if stripped != t and len(stripped) >= 20 and _word_count(stripped) >= 4:
        t = stripped
    # A full stop closing a printed title is punctuation, not a sentence.
    if t.endswith(".") and not t.endswith("..") and _word_count(t) >= 5:
        if not _ABBREV_TAIL_RE.search(t):
            t = t[:-1].rstrip()
    return t


def is_plausible_title(text: str) -> bool:
    t = strip_title_affixes(normalize(text))
    if not t or is_garbled_text(t) or is_weak_title(t):
        return False
    if is_cover_chrome(t) or is_journal_label(t) or looks_like_author_line(t):
        return False
    if looks_like_sentence(t):
        return False
    if _ADDRESS_RE.search(t) and _word_count(t) <= 10:   # <-- add the gate here
        return False
    if t[:1] in {"*", "#"} or t[:1].islower():
        return False
    if "@" in t or "http://" in t.lower() or "https://" in t.lower():
        return False
    if t.endswith((".", "?", "!")) and _word_count(t) >= 12:
        return False
    # Sentence run-on, but never trip on initialisms such as "C.I. Engine".
    if re.search(r"[a-z]{2,}\.\s+[A-Z]", t) and _word_count(t) >= 10:
        return False
    if _ALL_PARENS_RE.match(t) or _CITATION_LINE_RE.search(t):
        return False
    if re.search(r"\b(?:journal of physics|open access proceedings)\b", t, re.IGNORECASE):
        return False
    if _URL_EMAIL_RE.match(t) or is_caption(t) or is_known_heading(t):
        return False
    # "1.1 Introduction to Harvester" is a section heading, never the title.
    if is_numbered_heading(t) or _NUM_PREFIX_RE.match(t):
        return False
    words = [w for w in re.split(r"[\s/_\-—–|:·•]+", t) if re.search(r"[A-Za-z]", w)]
    if len(words) < 2:
        return False
    if words[-1].lower().strip("-,:") in _WRAP_TAIL_WORDS:
        return False
    return True

def _has_plausible_english_words(text: str, min_words: int = 20) -> bool:
    """Real English prose of any reasonable length contains common short
    function words. A systematically shifted/scrambled character encoding
    (e.g. a broken ToUnicode CMap) produces Latin letters that spell
    nothing — this catches that, which a pure Latin-character-ratio check
    cannot, since the shifted letters are still A-Z."""
    words = re.findall(r"[a-zA-Z]+", text.lower())
    if len(words) < min_words:
        return True  # too short to judge reliably; don't false-positive
    hits = sum(1 for w in words if w in _COMMON_ENGLISH_WORDS)
    return (hits / len(words)) >= 0.03

def quoted_cover_title(page: PageExtract) -> Optional[str]:
    blob = (page.text or "")[:2000].lower()
    if not any(
        marker in blob
        for marker in (
            "project report", "submitted in partial", "submitted by",
            "in partial fulfillment", "visvesvaraya", "minor project",
        )
    ):
        return None

    def _from_quoted(raw: str) -> Optional[str]:
        match = _QUOTED_TITLE_RE.search(raw or "")
        if not match:
            return None
        t = clean_candidate(match.group(1), allow_weak=False)
        if t and is_plausible_title(t):
            return t
        return None

    for ln in page.lines:
        found = _from_quoted(ln.text or "")
        if found:
            return found
    found = _from_quoted(page.text or "")
    if found:
        return found
    joined = " ".join(normalize(ln.text) for ln in page.lines[:60])
    return _from_quoted(joined)


def labeled_cover_title(page: PageExtract) -> Optional[str]:
    """Elsevier-style preprint covers print 'Title: ...' above 'Author: ...'."""
    lines = sorted(page.lines, key=lambda ln: (ln.y0, ln.x0))
    for i, ln in enumerate(lines):
        match = _LABELED_TITLE_RE.match(normalize(ln.text))
        if not match:
            continue
        parts = [normalize(match.group(1))]
        prev = ln
        for nxt in lines[i + 1:]:
            text = normalize(nxt.text)
            if not text:
                continue
            if _METADATA_LABEL_RE.match(text) or _LABELED_TITLE_RE.match(text):
                break
            if nxt.size < prev.size * WRAP_SIZE_RATIO:
                break
            if nxt.y0 - prev.y1 > max(prev.size * 1.4, 16):
                break
            if looks_like_author_line(text) or is_known_heading(text):
                break
            parts.append(text)
            prev = nxt
            if len(parts) >= TITLE_BAND_MAX_LINES:
                break
        candidate = clean_candidate(normalize(" ".join(parts)))
        if candidate and is_plausible_title(candidate):
            return candidate
    return None


def is_front_matter_page(page: PageExtract) -> bool:
    raw = (page.text or "")[:1400]
    raw = _VIEW_TOC_LINK_RE.sub("", raw)
    blob = canonical_header_key(raw)
    marker_hit = next((m for m in _FRONT_MATTER_MARKERS if m in blob), None)
    if marker_hit:
        return True
    if page.char_count < 420 and "journal" in blob:
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
    if is_garbled_text(t) or looks_like_author_line(t) or is_cover_chrome(t):
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
    return f"{primary} {secondary}"


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
    words = [w for w in t.split() if w[:1].isalpha()]
    if _word_count(t) > 14:
        letters = [c for c in t if c.isalpha()]
        caps = (sum(c.isupper() for c in letters) / len(letters)) if letters else 0.0
        small = _WRAP_TAIL_WORDS | {"instead", "into", "from"}
        titleish = bool(words) and all(
            w[0].isupper() or w.lower().strip("-,:") in small for w in words
        )
        if caps >= 0.55 or titleish:
            return False
        return True
    return False


def is_caption(text: str) -> bool:
    return bool(_CAPTION_RE.match(normalize(text)))


def is_citation_line(text: str) -> bool:
    """Volume/issue/page/ISSN/DOI line printed above or below an article title."""
    return bool(_CITATION_LINE_RE.search(normalize(text)))


def is_journal_label(text: str) -> bool:
    key = running_header_key(text)
    if key in _JOURNAL_LABELS:
        return True
    if key and _JOURNAL_LABEL_RE.match(key):
        return True
    if _PAREN_LABEL_RE.match(normalize(text)):
        inner = running_header_key(text)
        if inner in _JOURNAL_LABELS or inner.isupper():
            return True
    return False

def _is_print_header_line(text: str) -> bool:
    """Browser 'Print to PDF' stamps a timestamp + URL breadcrumb on every
    page — same line, verbatim, page after page. Never real content."""
    return bool(_PRINT_HEADER_RE.match(text.strip()))

def is_author_or_footnote(ln: Line) -> bool:
    text = normalize(ln.text)
    if is_numbered_heading(text) or is_known_heading(text):
        return False
    if looks_like_author_line(text):
        return True
    footer = ln.page_height > 0 and ln.y0 >= ln.page_height * 0.88
    if footer and ("corresponding" in text.lower() or "@" in text or "copyright" in text.lower()):
        return True
    return False


def is_noise_line(ln: Line, running: set[str]) -> bool:
    text = normalize(ln.text)
    if not text:
        return True
    if is_garbled_text(text) or is_font_artifact(ln) or is_cover_chrome(text):
        return True
    key = running_header_key(text)
    if key in running or canonical_header_key(text) in running:
        return True
    if _PAGE_NO_RE.match(text) or is_date_like(text) or _URL_EMAIL_RE.match(text):
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


def _horizontal_overlap_frac(a: Line, b: Line) -> float:
    overlap = min(a.x1, b.x1) - max(a.x0, b.x0)
    min_w = min(max(a.x1 - a.x0, 1.0), max(b.x1 - b.x0, 1.0))
    return overlap / min_w


def _font_base(name: str) -> str:
    return re.split(r"[-,+]", (name or "").lower())[0]


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
                if piece[:1].islower():
                    chunks[-1] = prev[:-1] + piece
                else:
                    chunks[-1] = prev + piece
                continue
        chunks.append(piece)
    text = normalize(" ".join(chunks)).strip(" \t-_:;")
    if len(text) > MAX_TITLE_LEN:
        text = text[:MAX_TITLE_LEN].rsplit(" ", 1)[0]
    return text


def _ends_with_wrap(text: str) -> bool:
    words = normalize(text).split()
    if not words:
        return False
    return words[-1].lower().strip("-,:") in _WRAP_TAIL_WORDS


def _strong_author_marks(text: str) -> bool:
    t = normalize(text)
    if not t:
        return False
    if t.count("#") >= 1 or t.count("*") >= 2:
        return True
    if _AUTHOR_MARK_RE.search(t) or _NUMBERED_PERSON_RE.search(t):
        return True
    if "@" in t:
        return True
    return False


def _is_rule_line(text: str) -> bool:
    t = normalize(text)
    return bool(t) and not re.search(r"[A-Za-z0-9]", t)

def _dedupe_overlapping_lines(lines: Sequence[Line]) -> List[Line]:
    """Same text drawn twice at (near-)identical bbox — a faux-bold or
    duplicated content-stream artifact, not two separate lines of content."""
    deduped: List[Line] = []
    for ln in lines:
        if deduped:
            prev = deduped[-1]
            same_text = normalize(prev.text) == normalize(ln.text)
            same_spot = (
                abs(prev.x0 - ln.x0) < 2
                and abs(prev.y0 - ln.y0) < 2
                and abs(prev.x1 - ln.x1) < 2
                and abs(prev.y1 - ln.y1) < 2
            )
            if same_text and same_spot:
                continue
        deduped.append(ln)
    return deduped

def _is_title_stop(ln: Line, prev: Optional[Line] = None) -> bool:
    text = normalize(ln.text)
    if not text:
        return True
    if _is_rule_line(text):
        return True
    if is_known_heading(text) or is_numbered_heading(text) or is_caption(text):
        return True
    if is_journal_label(text) or is_cover_chrome(text):
        return True
    if _TITLE_BLOCK_STOP_RE.match(text) or _JOURNAL_BANNER_RE.match(text):
        return True
    if _CITATION_LINE_RE.search(text) or _METADATA_LABEL_RE.match(text):
        return True
    if looks_like_author_line(text):
        if prev is not None and _ends_with_wrap(prev.text) and not _strong_author_marks(text):
            return False
        return True
    if text.count(",") >= 3 and _word_count(text) >= 6:
        return True
    return False


def _is_gap_skip(ln: Line, title_size: float, running: set[str]) -> bool:
    """Sidebar / watermark / tiny type sitting between wrapped title lines."""
    if is_font_artifact(ln) or is_garbled_text(ln.text):
        return True
    # Anything too small to be part of this wrap is a badge ("OPEN"), not a stop.
    # The gap ceiling in _same_wrap still prevents jumping past real content.
    if ln.size < title_size * WRAP_SIZE_RATIO:
        return True
    text = normalize(ln.text)
    if not text or _is_rule_line(text):
        return True
    if is_cover_chrome(text) or is_journal_label(text) or _URL_EMAIL_RE.match(text):
        return True
    if is_noise_line(ln, running) and not _is_title_stop(ln):
        return True
    return False


def _same_wrap(prev: Line, nxt: Line, title_size: float) -> bool:
    if _is_title_stop(nxt, prev=prev):
        return False
    gap = nxt.y0 - prev.y1
    if gap > max(title_size * 1.35, 16):
        return False
    if nxt.size < title_size * WRAP_SIZE_RATIO:
        return False
    prev_mid = (prev.x0 + prev.x1) / 2.0
    nxt_mid = (nxt.x0 + nxt.x1) / 2.0
    return (
        abs(nxt.x0 - prev.x0) <= 48
        or abs(nxt_mid - prev_mid) <= 64
        or abs(nxt.x1 - prev.x1) <= 48
        or (nxt.x0 >= prev.x0 - 8 and nxt.x1 <= prev.x1 + 8)
    )


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
    ordered = _dedupe_overlapping_lines(ordered)
    top = [ln for ln in ordered if ln.y0 <= page_h * VISUAL_TOP_FRACTION]
    usable = [
        ln for ln in top
        if not is_noise_line(ln, running)
        and not _is_title_stop(ln)
        and not looks_like_author_line(ln.text)
        and not is_weak_title(ln.text)
        and not is_font_artifact(ln)
        and not looks_like_sentence(ln.text)
    ]
    sized = [
        ln for ln in usable
        if body_size <= 0 or ln.size >= body_size * 1.06
    ]
    if sized:
        usable = sized
    if not usable:
        return TitleBand(context=_title_context(ordered))

    rejected: set[int] = set()
    for _ in range(4):
        pool = [ln for ln in usable if id(ln) not in rejected]
        if not pool:
            break
        band_lines = _grow_band(pool, ordered, running, rejected)
        if not band_lines:
            break
        if _is_masthead_band(band_lines, ordered, running, rejected):
            rejected.update(id(ln) for ln in band_lines)
            continue
        joined = strip_title_affixes(join_title_lines(band_lines))
        if looks_like_author_line(joined):
            rejected.update(id(ln) for ln in band_lines)
            continue
        return TitleBand(
            lines=band_lines,
            text=joined,
            context=_title_context(ordered, skip={id(ln) for ln in band_lines}),
            candidates=[joined] if joined else [],
            uncertain=not is_plausible_title(joined),
        )
    return TitleBand(context=_title_context(ordered))


def _grow_band(
    pool: Sequence[Line],
    ordered: Sequence[Line],
    running: set[str],
    rejected: set[int],
    seed: Optional[Line] = None,
) -> List[Line]:
    if seed is None:
        max_size = max(ln.size for ln in pool)
        seeds = [ln for ln in pool if ln.size >= max_size * SEED_SIZE_RATIO]
        if not seeds:
            seeds = list(pool)
        seed = min(seeds, key=lambda ln: ln.y0)
    title_size = seed.size

    try:
        idx = ordered.index(seed)
    except ValueError:
        return []

    def skippable(ln: Line) -> bool:
        return id(ln) in rejected or _is_gap_skip(ln, title_size, running)

    band_lines = [seed]
    cursor = idx
    while len(band_lines) < TITLE_BAND_MAX_LINES:
        j = cursor - 1
        while j >= 0 and skippable(ordered[j]):
            j -= 1
        if j < 0:
            break
        cand = ordered[j]
        if is_noise_line(cand, running) or _is_title_stop(cand):
            break
        if not _same_wrap(cand, band_lines[0], title_size):
            break
        band_lines.insert(0, cand)
        cursor = j

    cursor = idx
    while len(band_lines) < TITLE_BAND_MAX_LINES:
        j = cursor + 1
        while j < len(ordered) and skippable(ordered[j]):
            j += 1
        if j >= len(ordered):
            break
        cand = ordered[j]
        if is_noise_line(cand, running) or _is_title_stop(cand, prev=band_lines[-1]):
            break
        if not _same_wrap(band_lines[-1], cand, title_size):
            break
        band_lines.append(cand)
        cursor = j

    return _repair_wrap_tail(band_lines, ordered, title_size, running)


def _is_masthead_band(
    band_lines: Sequence[Line],
    ordered: Sequence[Line],
    running: set[str],
    rejected: set[int],
) -> bool:
    """True when the band is journal branding rather than the article title."""
    if not band_lines:
        return True
    joined = normalize(join_title_lines(band_lines))
    if not joined:
        return True
    if is_cover_chrome(joined) or is_journal_label(joined) or _JOURNAL_BANNER_RE.match(joined):
        return True
    if _article_title_below(band_lines, ordered, running):
        return True
    # The band may be the tail of a masthead whose first line was filtered out.
    size = band_lines[0].size
    try:
        idx = ordered.index(band_lines[0])
    except ValueError:
        return False
    seen = 0
    for j in range(idx - 1, -1, -1):
        cand = ordered[j]
        if id(cand) in rejected:
            continue
        if cand.size < size * WRAP_SIZE_RATIO:
            continue
        # Larger type above is a separate block. Journal papers normally print
        # the masthead directly above the title, so only same-size text can be
        # the banner this band belongs to.
        if cand.size > size / WRAP_SIZE_RATIO:
            return False
        if band_lines[0].y0 - cand.y1 > max(size * 1.6, 20):
            return False
        text = normalize(cand.text)
        if not text:
            continue
        # Only a publication banner marks the band below it as masthead text.
        # Article-type labels ("Original Research Paper") sit above real titles.
        if _JOURNAL_BANNER_RE.match(text):
            return True
        return False
    return False


def _article_title_below(
    band_lines: Sequence[Line],
    ordered: Sequence[Line],
    running: set[str],
) -> bool:
    """True when a short banner sits above a longer same-size article title."""
    if not band_lines:
        return False
    joined = join_title_lines(band_lines)
    band_words = _word_count(joined)
    if band_words >= 10:
        return False
    last = band_lines[-1]
    try:
        idx = ordered.index(last)
    except ValueError:
        return False
    band_size = max(ln.size for ln in band_lines)
    for ln in ordered[idx + 1: idx + 18]:
        if ln.y0 - last.y1 > max(band_size * 10, 90):
            break
        text = normalize(ln.text)
        if not text:
            continue
        if is_known_heading(text) or _TITLE_BLOCK_STOP_RE.match(text):
            return False
        if (
            is_noise_line(ln, running)
            or looks_like_author_line(text)
            or looks_like_sentence(text)
            or _JOURNAL_BANNER_RE.match(text)
        ):
            continue
        words = _word_count(text)
        if (
            ln.size >= band_size * 0.86
            and words >= 6
            and is_plausible_title(strip_title_affixes(text))
            and (words > band_words or ln.size >= band_size * 0.95)
        ):
            return True
    return False


def _repair_wrap_tail(
    band_lines: List[Line],
    ordered: Sequence[Line],
    title_size: float,
    running: set[str],
) -> List[Line]:
    """Continue a band that stopped mid-phrase, e.g. '... Radiator by Using'.

    A title ending in a preposition is always truncated, so a line the stop
    rules vetoed is more likely a misread title tail than real content. Only
    same-size, closely-spaced lines qualify, so this cannot run into body text.
    """
    if not band_lines or not _ends_with_wrap(join_title_lines(band_lines)):
        return band_lines
    try:
        cursor = ordered.index(band_lines[-1])
    except ValueError:
        return band_lines
    for _ in range(2):
        j = cursor + 1
        while j < len(ordered) and _is_gap_skip(ordered[j], title_size, running):
            j += 1
        if j >= len(ordered):
            break
        cand = ordered[j]
        prev = band_lines[-1]
        if cand.size < title_size * WRAP_SIZE_RATIO:
            break
        if cand.y0 - prev.y1 > max(title_size * 1.2, 14):
            break
        if _horizontal_overlap_frac(prev, cand) < 0.28:
            break
        text = normalize(cand.text)
        if not text or is_known_heading(text) or is_caption(text):
            break
        if is_cover_chrome(text) or _CITATION_LINE_RE.search(text):
            break
        if _METADATA_LABEL_RE.match(text) or is_noise_line(cand, running):
            break
        if _strong_author_marks(text) or looks_like_sentence(text):
            break
        band_lines.append(cand)
        cursor = j
        if not _ends_with_wrap(join_title_lines(band_lines)):
            break
    return band_lines


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
                similar = abs(nxt.size - ln.size) <= max(0.8, ln.size * 0.08)
                overlap_ok = _horizontal_overlap_frac(ln, nxt) >= 0.28
                if (
                    secondary
                    and similar
                    and overlap_ok
                    and not looks_like_sentence(secondary)
                    and not _is_title_stop(nxt, prev=ln)
                    and (
                        is_date_like(secondary)
                        or len(secondary.split()) <= 8
                        or _ends_with_wrap(text)
                    )
                ):
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
            if t and not _CITATION_LINE_RE.search(t) and not _ALL_PARENS_RE.match(t):
                return t
        for raw in page.text.split("\n"):
            t = clean_candidate(raw)
            if not t or looks_like_sentence(t):
                continue
            if _CITATION_LINE_RE.search(t) or _ALL_PARENS_RE.match(t):
                continue
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


def _select_scan_pages(pages: Sequence[PageExtract]) -> List[PageExtract]:
    selected: List[PageExtract] = []
    skipped: List[PageExtract] = []
    for page in pages:
        if not selected and len(skipped) < MAX_FRONT_MATTER_PAGES and is_front_matter_page(page):
            skipped.append(page)
            continue
        if selected and _word_count(page.text) < 20:
            continue
        selected.append(page)
        if len(selected) >= 4:
            break
    if selected:
        return selected
    return skipped[:1] or list(pages[:1])


def _band_quality(band: TitleBand) -> float:
    text = strip_title_affixes(band.text or "")
    if not text or looks_like_author_line(text) or is_journal_label(text) or is_cover_chrome(text):
        return -1.0
    score = 0.0
    if is_plausible_title(text):
        score += 4.0
    elif _is_partial_title(text):
        score += 1.5
    if band.lines:
        score += min(max(ln.size for ln in band.lines) / 10.0, 2.5)
    words = _word_count(text)
    if 4 <= words <= 20:
        score += 1.2
    elif words < 3:
        score -= 0.8
    return score


def resolve_document_title(
    doc: NativeDocument,
    *,
    filename: Optional[str] = None,
) -> Tuple[str, str, TitleBand]:

    pages = doc.pages
    if pages:
        print(f"[page0_check] char_count={pages[0].char_count} text_preview={pages[0].text[:200]!r}")
    scan = _select_scan_pages(pages)
    print(f"[scan_pages] selected {len(scan)} page(s)")
    for p in scan:
        print(f"[scan_pages] page={p.page} char_count={p.char_count} text_preview={p.text[:80]!r}")
    all_lines = [ln for p in pages for ln in p.lines]
    body = body_font_size(all_lines)
    running = _running_header_keys(pages)
    band = TitleBand()
    if scan:
        best_score = -2.0
        for page in scan:
            candidate = extract_title_band(page, body, running=set())
            score = _band_quality(candidate)
            if score > best_score:
                best_score = score
                band = candidate
        print("\n" + "=" * 80)
        print("TITLE BAND DEBUG")
        print("=" * 80)

        print("BAND TEXT:")
        print(repr(band.text))

        print("\nBAND LINES:")
        for i, ln in enumerate(band.lines):
           print(
               f"[{i}] text={ln.text!r} "
               f"size={ln.size} "
               f"bold={ln.bold} "
               f"font={ln.font!r} "
               f"bbox=({ln.x0:.1f}, {ln.y0:.1f}, {ln.x1:.1f}, {ln.y1:.1f})"
           )

        print("=" * 80)

    heading_hint = None
    if doc.toc:
        heading_hint = clean_candidate(doc.toc[0][1], allow_weak=True)
        if heading_hint and (is_journal_label(heading_hint) or is_caption(heading_hint) or not is_plausible_title(heading_hint)):
            heading_hint = None

    quoted = quoted_cover_title(scan[0]) if scan else None
    labeled = None
    for page in scan:
        labeled = labeled_cover_title(page)
        if labeled:
            break

    meta = clean_candidate(doc.meta_title)
    if meta and not is_plausible_title(meta):
        meta = None

    cleaned_band = strip_title_affixes(band.text) if band.text else ""
    print("\n" + "=" * 80)
    print("TITLE BAND FILTER DEBUG")
    print("=" * 80)
    print(f"band.text              = {band.text!r}")
    print(f"cleaned_band           = {cleaned_band!r}")
    print(f"word_count             = {len(cleaned_band.split())}")
    print(f"is_plausible_title     = {is_plausible_title(cleaned_band)}")

    if band.lines:
       print(f"band_line_count        = {len(band.lines)}")
       print(f"band_first_line        = {band.lines[0].text!r}")
       print(f"band_first_line_size   = {band.lines[0].size}")
       print(f"band_first_line_bold   = {band.lines[0].bold}")
       print(f"band_first_line_font   = {band.lines[0].font!r}")

    print("=" * 80)

    ranked: List[Tuple[float, str, str]] = []
    seen_keys: set[str] = set()

    def consider(text: Optional[str], source: str, bonus: float = 0.0) -> None:
        if not text:
            return
        cleaned = strip_title_affixes(text)
        if not cleaned:
            return
        if is_journal_label(cleaned) or is_cover_chrome(cleaned) or is_caption(cleaned):
            return
        if is_known_heading(cleaned) or looks_like_author_line(cleaned):
            return
        if looks_like_sentence(cleaned):
            return
        key = canonical_header_key(cleaned)
        if not key or key in seen_keys:
            return
        plausible = is_plausible_title(cleaned)
        partial = _is_partial_title(cleaned)
        if not plausible and not partial:
            weak = clean_candidate(cleaned, allow_weak=True)
            if not weak or source not in {"metadata", "toc"}:
                return
            cleaned = weak
            key = canonical_header_key(cleaned)
            if not key or key in seen_keys:
                return
        score = bonus + _text_title_score(cleaned, source)
        seen_keys.add(key)
        ranked.append((score, cleaned, source))

    consider(quoted, "visual", 2.6)
    consider(labeled, "visual", 2.6)
    consider(cleaned_band, "visual", 1.8)
    for alt in band.candidates:
        consider(alt, "visual", 1.2)
    for page in scan:
        consider(visual_title(page, body, running), "visual", 1.0)
    consider(heading_hint, "toc", 0.35)
    if meta:
        page_blob = " ".join(p.text or "" for p in scan[:2])
        supported = canonical_header_key(meta) in canonical_header_key(page_blob)
        consider(meta, "metadata", 0.7 if supported else 0.15)
    if cleaned_band and _is_partial_title(cleaned_band):
        consider(cleaned_band, "visual", 0.9)
    consider(first_content_line(scan or pages, running), "content", 0.2)

    if not ranked:
        print(">>> TITLE BAND DID NOT PASS FILTER")
        return "Untitled document", "none", band

    ranked.sort(key=lambda row: row[0], reverse=True)
    best_score, winner, source = ranked[0]
    agreed = _reconcile_with_metadata(winner, meta)
    if agreed != winner:
        winner, source = agreed, "metadata"

    visual_band = strip_title_affixes(band.text) if band.text else ""
    alts: List[str] = []
    alt_keys: set[str] = set()
    for _sc, text, _src in ranked:
        key = canonical_header_key(text)
        if not key or key in alt_keys:
            continue
        alt_keys.add(key)
        alts.append(text)
    if visual_band:
        visual_key = canonical_header_key(visual_band)
        if visual_key and visual_key not in alt_keys:
            alts.insert(0, visual_band)
    band.candidates = alts
    band.uncertain = (
        not is_plausible_title(winner)
        or looks_like_author_line(winner)
        or looks_like_sentence(winner)
        or source in {"content", "none"}
    )
    print(f">>> RETURNING: {winner!r} source={source} uncertain={band.uncertain}")
    return winner, source, band


def _text_title_score(text: str, source: str) -> float:
    score = 0.0
    if is_plausible_title(text):
        score += 3.0
    elif _is_partial_title(text):
        score += 1.2
    else:
        score -= 0.8
    words = _word_count(text)
    if 4 <= words <= 18:
        score += 1.0
    elif words < 3:
        score -= 0.8
    elif words > 24:
        score -= 0.6
    if source == "visual":
        score += 0.2
    return score


def _reconcile_with_metadata(band_text: str, meta: Optional[str]) -> str:
    """Prefer the embedded title when it is the band minus surrounding noise."""
    if not meta:
        return band_text
    a = canonical_header_key(band_text)
    b = canonical_header_key(meta)
    if not a or not b or a == b:
        return band_text
    if b in a and len(b) >= max(20, len(a) * 0.5):
        return meta
    return band_text


def _is_partial_title(text: str) -> bool:
    """Real but unfinished title text: right words, cut off at a wrap point."""
    t = normalize(text)
    if not t or len(t) < 12 or len(t) > MAX_TITLE_LEN or is_garbled_text(t):
        return False
    if not t[:1].isupper():
        return False
    if looks_like_sentence(t) or t.endswith((".", "!", "?")):
        return False
    if is_cover_chrome(t) or is_journal_label(t) or looks_like_author_line(t):
        return False
    if _CITATION_LINE_RE.search(t) or _ALL_PARENS_RE.match(t):
        return False
    if "@" in t or _URL_EMAIL_RE.match(t) or is_caption(t) or is_known_heading(t):
        return False
    words = [w for w in re.split(r"[\s/_\-—–|:·•]+", t) if re.search(r"[A-Za-z]", w)]
    return 3 <= len(words) <= 25


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
