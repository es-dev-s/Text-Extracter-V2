"""Async Groq call: exact engineering/CDR assignment title from list + opening excerpt."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import time
from typing import Iterable, Optional, Sequence

import httpx

from ..config import GROQ_CHAT_URL, groq_api_keys, groq_enabled, groq_model
from .headings import (
    TitleBand,
    canonical_header_key,
    is_known_heading,
    is_plausible_title,
    join_title_lines,
    normalize,
    strip_title_affixes,
)
from .native import PageExtract

logger = logging.getLogger("engine.groq")
_key_cursor = 0
_cooldown_until: dict[str, float] = {}

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)
_INDEX_PREFIX_RE = re.compile(r"^\s*\d+[.)]\s+")
EXCERPT_LIMIT = 1000
_RUNON_SECTIONS = frozenset({
    "abstract", "abstract:", "keywords", "keyword", "key words", "introduction",
    "references", "bibliography", "acknowledgment", "acknowledgments",
    "acknowledgement", "acknowledgements", "nomenclature", "contents",
    "table of contents", "graphical abstract", "highlights",
})
_WRAP_TAILS = {
    "and", "or", "of", "for", "the", "a", "an", "to", "with", "in", "on",
    "by", "from", "into", "using", "via", "at", "as", "over", "under",
}


def opening_excerpt(pages: Sequence[PageExtract], limit: int = EXCERPT_LIMIT) -> str:
    """Up to `limit` characters: page 1, then 2, then 3, until the budget is filled."""
    parts: list[str] = []
    for page in pages:
        text = re.sub(r"\s+", " ", page.text or "").strip()
        if not text:
            continue
        parts.append(text)
        if len(" ".join(parts)) >= limit:
            break
    blob = " ".join(parts).strip()
    if len(blob) <= limit:
        return blob
    cut = blob[:limit]
    if blob[limit].isalnum() and cut[-1:].isalnum():
        cut = cut.rsplit(" ", 1)[0]
    return cut.strip()


def _is_incomplete_title(text: str) -> bool:
    t = freeze_title(text)
    if not t or not is_plausible_title(t):
        return True
    last = t.split()[-1].lower().strip("-,:")
    return last in _WRAP_TAILS


def _parse_json_object(raw: str) -> Optional[dict]:
    if not raw:
        return None
    text = _FENCE_RE.sub("", raw.strip()).strip()
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            try:
                data = json.loads(text[start : end + 1])
                return data if isinstance(data, dict) else None
            except json.JSONDecodeError:
                return None
        return None


def _words(text: str) -> list[str]:
    return re.findall(r"[A-Za-z0-9]+", text.lower())


def _strip_index(text: str) -> str:
    return _INDEX_PREFIX_RE.sub("", normalize(text))


def strip_section_tails(text: str) -> str:
    """Drop a run-on section heading, e.g. '... of a Radiator Abstract Keywords'.

    Only headings that cannot end a real title qualify. Words such as
    'Methodology' or 'Design' are section names too, but they legitimately close
    titles like '... using Taguchi methodology', so they are left alone.
    """
    tokens = normalize(text).split()
    while len(tokens) > 3:
        stripped = False
        for n in range(min(3, len(tokens) - 3), 0, -1):
            tail = " ".join(tokens[-n:]).lower().strip(" .:-")
            tail_plain = re.sub(r"^\d+\.?\s*", "", tail)
            if tail in _RUNON_SECTIONS or tail_plain in _RUNON_SECTIONS:
                tokens = tokens[:-n]
                stripped = True
                break
        if not stripped:
            break
    return " ".join(tokens)


def recover_span(chosen: str, source: str) -> Optional[str]:
    """Return the source's original wording for the chosen words, in order."""
    tokens = re.findall(r"\S+", _strip_index(chosen))
    if len(tokens) < 2:
        return None
    pattern = r"\s+".join(re.escape(tok) for tok in tokens)
    match = re.search(pattern, source, flags=re.IGNORECASE)
    if match:
        return normalize(match.group(0))
    return None


def _extra_is_section(base_key: str, full_key: str) -> bool:
    if not full_key.startswith(base_key + " "):
        return False
    extra = full_key[len(base_key):].strip()
    if not extra:
        return False
    if is_known_heading(extra):
        return True
    return any(is_known_heading(part) for part in extra.split())


def _candidate_pool(
    band: TitleBand,
    headers: Sequence[str],
) -> list[str]:
    pool: list[str] = []
    seen: set[str] = set()

    def add(text: str) -> None:
        t = strip_title_affixes(strip_section_tails(_strip_index(text)))
        if not t or is_known_heading(t) or not is_plausible_title(t):
            return
        key = canonical_header_key(t)
        if not key or key in seen:
            return
        seen.add(key)
        pool.append(t)

    if band.text:
        add(band.text)
    if band.lines:
        for i in range(len(band.lines)):
            for j in range(i, len(band.lines)):
                add(join_title_lines(band.lines[i : j + 1]))
    for item in headers:
        add(item)
    return pool


def freeze_title(text: str) -> str:
    """Canonical string so the same PDF always yields the same title bytes."""
    return strip_title_affixes(strip_section_tails(_strip_index(normalize(text or ""))))


def complete_from_list(chosen: str, pool: Sequence[str]) -> Optional[str]:
    """If a half title exists in full on the list, use the full item."""
    picked = strip_section_tails(_strip_index(chosen))
    if not picked:
        return None
    key = canonical_header_key(picked)
    exact = None
    longer = None
    for cand in pool:
        ck = canonical_header_key(cand)
        if ck == key:
            exact = cand
        elif ck.startswith(key + " ") and not _extra_is_section(key, ck):
            if longer is None or len(ck) > len(canonical_header_key(longer)):
                longer = cand
    return longer or exact


def _best_plausible(text: str, pool: Sequence[str]) -> Optional[str]:
    ordered: list[str] = []
    longer = complete_from_list(text, pool)
    if longer:
        ordered.append(longer)
    for candidate in (text, strip_title_affixes(text)):
        if candidate:
            ordered.append(candidate)
    ordered.extend(pool)
    seen: set[str] = set()
    for candidate in ordered:
        cleaned = freeze_title(candidate)
        key = canonical_header_key(cleaned)
        if not key or key in seen:
            continue
        seen.add(key)
        if cleaned and is_plausible_title(cleaned):
            return cleaned
    return None


def echoes_filename(chosen: str, filename: str) -> bool:
    """Guard the vision path: a title must come from the page, never the filename."""
    stem = re.sub(r"\.[A-Za-z0-9]{1,5}$", "", filename or "")
    stem_words = _words(re.sub(r"[_\-.]+", " ", stem))
    picked = _words(chosen)
    if not picked or not stem_words:
        return False
    if picked == stem_words:
        return True
    overlap = len(set(picked) & set(stem_words))
    return overlap == len(set(picked)) and len(picked) <= 6


def grounded_in_sources(chosen: str, *sources: str) -> bool:
    allowed = _words(" ".join(s for s in sources if s))
    picked = _words(chosen)
    if not picked or not allowed:
        return False
    counts: dict[str, int] = {}
    for word in allowed:
        counts[word] = counts.get(word, 0) + 1
    for word in picked:
        if counts.get(word, 0) <= 0:
            return False
        counts[word] -= 1
    return True


def _accept_ai_title(
    native: str,
    chosen: str,
    pool: Sequence[str],
    sources: Sequence[str],
) -> Optional[str]:
    """Keep native when it is already complete; allow AI only to confirm or fill a half title."""
    native_f = freeze_title(native)
    chosen_f = freeze_title(chosen)
    if not chosen_f:
        return None

    recovered = chosen_f
    for source in sources:
        found = recover_span(chosen_f, source)
        if found:
            recovered = found
            break
    completed = freeze_title(complete_from_list(recovered, pool) or recovered)
    if not completed or not is_plausible_title(completed):
        return None
    if not grounded_in_sources(completed, *sources):
        return None

    native_key = canonical_header_key(native_f)
    chosen_key = canonical_header_key(completed)
    native_incomplete = _is_incomplete_title(native_f)

    if not native_incomplete and is_plausible_title(native_f):
        if chosen_key == native_key:
            return native_f
        if native_key and chosen_key.startswith(native_key + " "):
            return completed
        return None

    if native_key and (chosen_key == native_key or chosen_key.startswith(native_key + " ")):
        return completed
    return completed


def _build_messages(
    *,
    filename: str,
    heuristic_title: str,
    band: TitleBand,
    headers: Sequence[str],
    excerpt: str,
) -> list[dict]:
    band_lines = "\n".join(
        f"{i}. {normalize(ln.text)}" for i, ln in enumerate(band.lines, start=1)
    ) or "(none)"
    listed = "\n".join(
        f"{i}. {_strip_index(h)}" for i, h in enumerate(headers, start=1)
    ) or "(none)"
    return [
        {
            "role": "system",
            "content": (
                "You verify the official printed title of an engineering assignment "
                "or CDR (Competency Demonstration Report) PDF. "
                "The native title is a layout validation already extracted from the PDF. "
                "The text block is up to 1000 characters copied from the PDF "
                "(page 1, then page 2, then page 3, until 1000 characters). "
                "Return the complete title exactly as printed. "
                "If the native title is already the full printed title, copy it EXACTLY "
                "— same words and spelling; do not shorten or replace it. "
                "If the native title is half (cuts off, ends with and/of/for/the, or is "
                "cover chrome such as Accepted Manuscript), complete or correct it using "
                "ONLY words that appear in the native title, title-band, header list, or "
                "the text block. Never invent words. Never add authors, emails, URLs, "
                "journal names, university names, campus addresses, Abstract, Keywords, "
                "Introduction, or References. JSON only."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Filename: {filename}\n\n"
                f"NATIVE TITLE (layout validation I found):\n{heuristic_title}\n\n"
                f"Detected headers / title list:\n{listed}\n\n"
                f"Title-band lines (large text at the top):\n{band_lines}\n\n"
                f"TEXT BLOCK (up to 1000 characters from the PDF, page 1 then 2 then 3…):\n"
                f"{excerpt}\n\n"
                "Return JSON: "
                '{"title": "<full exact printed title>"}'
            ),
        },
    ]


def _rotated_keys() -> list[str]:
    """Ready keys first (round-robin). Cooling keys are last-resort, never slept on."""
    global _key_cursor
    keys = list(groq_api_keys())
    if not keys:
        return []
    now = time.monotonic()
    start = _key_cursor % len(keys)
    _key_cursor += 1
    ordered = keys[start:] + keys[:start]
    ready = [key for key in ordered if now >= _cooldown_until.get(key, 0.0)]
    return ready or ordered


def _cool(key: str, seconds: float) -> None:
    _cooldown_until[key] = time.monotonic() + max(1.0, min(seconds, 90.0))


def _retry_after(response: httpx.Response) -> float:
    raw = response.headers.get("retry-after") or "15"
    try:
        return float(raw)
    except ValueError:
        return 15.0


def _shortest_cooldown() -> float:
    """Seconds until the first key frees up, clamped so a caller never stalls."""
    now = time.monotonic()
    waits = [_cooldown_until.get(key, 0.0) - now for key in groq_api_keys()]
    waits = [w for w in waits if w > 0]
    if not waits:
        return 1.0
    return max(0.5, min(min(waits), 4.0))


async def _groq_content(
    http: httpx.AsyncClient,
    payload: dict,
    *,
    attempts: int = 1,
) -> Optional[str]:
    """`attempts` > 1 waits out a rate limit; use it only for last-resort calls."""
    for attempt in range(max(1, attempts)):
        content = await _groq_attempt(http, payload)
        if content:
            return content
        if attempt + 1 < attempts:
            await asyncio.sleep(_shortest_cooldown())
    return None


async def _groq_attempt(http: httpx.AsyncClient, payload: dict) -> Optional[str]:
    keys = _rotated_keys()
    if not keys:
        return None
    pool = groq_api_keys()
    total = len(pool)
    last_status: Optional[int] = None
    for key in keys:
        slot = pool.index(key) + 1 if key in pool else 0
        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        }
        try:
            response = await http.post(GROQ_CHAT_URL, headers=headers, json=payload)
        except httpx.TimeoutException:
            logger.warning("Groq key %s/%s timed out; next key", slot, total)
            _cool(key, 8.0)
            continue
        except httpx.RequestError:
            logger.warning("Groq key %s/%s failed; next key", slot, total)
            _cool(key, 8.0)
            continue
        last_status = response.status_code
        if response.status_code == 429:
            logger.warning("Groq key %s/%s rate limited; next key", slot, total)
            _cool(key, _retry_after(response))
            continue
        if response.status_code in {401, 403}:
            logger.warning("Groq key %s/%s rejected; next key", slot, total)
            _cool(key, 90.0)
            continue
        if response.status_code >= 500:
            logger.warning("Groq key %s/%s HTTP %s; next key", slot, total, response.status_code)
            _cool(key, 5.0)
            continue
        if response.status_code >= 400:
            logger.warning("Groq HTTP %s", response.status_code)
            return None
        try:
            body = response.json()
        except ValueError:
            logger.warning("Groq key %s/%s non-JSON; next key", slot, total)
            continue
        content = (
            ((body.get("choices") or [{}])[0].get("message") or {}).get("content")
            or ""
        )
        if content:
            _cooldown_until.pop(key, None)
            return content
        logger.warning("Groq key %s/%s empty body; next key", slot, total)
    logger.warning("Groq all keys failed last=%s", last_status)
    return None


async def pick_title_from_page_image(
    http: httpx.AsyncClient,
    *,
    jpeg: bytes,
    filename: str,
    native_title: str = "",
    excerpt: str = "",
    patient: bool = True,
) -> Optional[str]:
    """Read the printed title from a small page-1 JPEG. Used only when text OCR failed."""
    if not jpeg or not groq_enabled() or len(jpeg) > 900_000:
        return None
    data_url = "data:image/jpeg;base64," + base64.b64encode(jpeg).decode("ascii")
    safe_excerpt = re.sub(r"[^\x20-\x7E]+", " ", excerpt or "")
    safe_excerpt = re.sub(r"\s+", " ", safe_excerpt).strip()[:400]
    native_guess = native_title if native_title and native_title.lower() not in {
        "untitled document", "untitled", "no ocr",
    } else "(none)"
    payload = {
        "model": groq_model(),
        "messages": [
            {
                "role": "system",
                "content": (
                    "Read the official printed document title from the top of this first page. "
                    "Return that title exactly as printed. Do not add authors, emails, journal "
                    "names, Abstract, or Keywords. JSON only."
                ),
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            f"Filename: {filename}\n"
                            f"Native title guess (may be wrong): {native_guess}\n"
                            f"Opening text (may be garbled): {safe_excerpt or '(none)'}\n"
                            'Return JSON: {"title": "<full exact printed title>"}'
                        ),
                    },
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            },
        ],
        "temperature": 0.0,
        "top_p": 1e-9,
        "seed": 0,
        "max_completion_tokens": 180,
        "reasoning_effort": "none",
        "response_format": {"type": "json_object"},
    }
    # Worth waiting out a rate limit only when nothing else can read the page.
    content = await _groq_content(http, payload, attempts=3 if patient else 1)
    if not content:
        return None
    data = _parse_json_object(content) or {}
    chosen = freeze_title(str(data.get("title") or ""))
    if not chosen or not is_plausible_title(chosen):
        return None
    if echoes_filename(chosen, filename):
        return None
    return chosen


async def pick_pdf_title(
    http: httpx.AsyncClient,
    *,
    filename: str,
    heuristic_title: str,
    band: TitleBand,
    headers: Iterable[str] | None = None,
    excerpt: str = "",
) -> tuple[str, str]:
    header_list = [_strip_index(h) for h in (headers or []) if h]
    # The resolver already weighed the band against metadata, TOC and layout,
    # so its answer wins; the band is only a candidate source from here on.
    fallback = freeze_title(heuristic_title or band.text)
    pool = _candidate_pool(band, header_list)
    locked = _best_plausible(fallback, pool) or fallback
    sources = (
        excerpt,
        " ".join(header_list),
        join_title_lines(band.lines) if band.lines else "",
        fallback,
    )

    if not groq_enabled():
        return locked, "heuristic"

    payload = {
        "model": groq_model(),
        "messages": _build_messages(
            filename=filename or "document.pdf",
            heuristic_title=locked or fallback,
            band=band,
            headers=header_list,
            excerpt=excerpt,
        ),
        "temperature": 0.0,
        "top_p": 1e-9,
        "seed": 0,
        "max_completion_tokens": 220,
        "reasoning_effort": "none",
        "response_format": {"type": "json_object"},
    }
    content = await _groq_content(http, payload)
    if not content:
        return locked, "heuristic"

    data = _parse_json_object(content) or {}
    chosen = freeze_title(str(data.get("title") or ""))
    if not chosen:
        start, end = data.get("start"), data.get("end")
        try:
            start_i, end_i = int(start), int(end)
            if band.lines and 1 <= start_i <= end_i <= len(band.lines):
                chosen = freeze_title(join_title_lines(band.lines[start_i - 1 : end_i]))
        except (TypeError, ValueError):
            chosen = ""

    accepted = _accept_ai_title(locked, chosen, pool, sources) if chosen else None
    if accepted:
        return accepted, "groq"
    return locked, "heuristic"
