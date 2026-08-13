"""Async Groq call: exact engineering/CDR assignment title from list + opening excerpt."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Iterable, Optional, Sequence

import httpx

from ..config import GROQ_CHAT_URL, groq_api_key, groq_enabled, groq_model
from .headings import (
    TitleBand,
    canonical_header_key,
    is_known_heading,
    join_title_lines,
    normalize,
)
from .native import PageExtract

logger = logging.getLogger("engine.groq")

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)
_INDEX_PREFIX_RE = re.compile(r"^\s*\d+[.)]\s+")
EXCERPT_LIMIT = 500


def opening_excerpt(pages: Sequence[PageExtract], limit: int = EXCERPT_LIMIT) -> str:
    """~500 characters from page 1; fill from page 2 if page 1 is shorter."""
    parts: list[str] = []
    for page in pages[:2]:
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
    if len(blob) > limit and blob[limit].isalnum() and cut[-1:].isalnum():
        cut = cut.rsplit(" ", 1)[0]
    return cut.strip()


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
    """Drop trailing Abstract/Keywords/Introduction (and numbered forms) from a title."""
    tokens = normalize(text).split()
    while tokens:
        stripped = False
        for n in range(min(6, len(tokens)), 0, -1):
            tail = " ".join(tokens[-n:])
            tail_plain = re.sub(r"^\d+\.\s*", "", tail)
            if is_known_heading(tail) or is_known_heading(tail_plain):
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
        t = strip_section_tails(_strip_index(text))
        if not t or is_known_heading(t):
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


def complete_from_list(chosen: str, pool: Sequence[str]) -> Optional[str]:
    """If the model returned a half title that exists in full on the list, use the full item."""
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
                "You extract the official title of an engineering assignment or "
                "CDR (Competency Demonstration Report) submission. "
                "Return the complete title exactly as printed — never a half line. "
                "If the full title is already in the numbered list, copy that one list "
                "item exactly. If the printed title wraps across title-band lines, join "
                "only those consecutive band lines. "
                "Use only words that appear in the list, title-band, or opening excerpt. "
                "Do not invent words. Never append Abstract, Keywords, Introduction, "
                "References, author names, or university names to the title. "
                "JSON only."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Filename: {filename}\n"
                f"Heuristic title (may be truncated): {heuristic_title}\n\n"
                f"Detected headers / title list:\n{listed}\n\n"
                f"Title-band lines (large text at the top):\n{band_lines}\n\n"
                f"Opening excerpt (~500 characters from page 1, then page 2 if needed):\n"
                f"{excerpt}\n\n"
                "Return JSON: "
                '{"title": "<full exact assignment title>"}'
            ),
        },
    ]


async def _groq_content(http: httpx.AsyncClient, payload: dict) -> Optional[str]:
    headers = {
        "Authorization": f"Bearer {groq_api_key()}",
        "Content-Type": "application/json",
    }
    last_status: Optional[int] = None
    for attempt in range(2):
        try:
            response = await http.post(GROQ_CHAT_URL, headers=headers, json=payload)
        except httpx.TimeoutException:
            logger.warning("Groq request timed out")
            return None
        except httpx.RequestError:
            logger.warning("Groq request failed")
            return None
        last_status = response.status_code
        if response.status_code == 429 and attempt == 0:
            raw = response.headers.get("retry-after") or "1.5"
            try:
                delay = min(max(float(raw), 0.5), 4.0)
            except ValueError:
                delay = 1.5
            logger.warning("Groq rate limited; retrying in %.1fs", delay)
            await asyncio.sleep(delay)
            continue
        if response.status_code >= 400:
            logger.warning("Groq HTTP %s", response.status_code)
            return None
        try:
            body = response.json()
        except ValueError:
            logger.warning("Groq returned non-JSON")
            return None
        return (
            ((body.get("choices") or [{}])[0].get("message") or {}).get("content")
            or ""
        )
    logger.warning("Groq HTTP %s", last_status)
    return None


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
    fallback = band.text or heuristic_title
    pool = _candidate_pool(band, header_list)
    sources = excerpt, " ".join(header_list), join_title_lines(band.lines) if band.lines else ""

    if not groq_enabled():
        return complete_from_list(fallback, pool) or fallback, "heuristic"

    payload = {
        "model": groq_model(),
        "messages": _build_messages(
            filename=filename or "document.pdf",
            heuristic_title=fallback,
            band=band,
            headers=header_list,
            excerpt=excerpt,
        ),
        "temperature": 0.0,
        "max_completion_tokens": 220,
        "reasoning_effort": "none",
        "response_format": {"type": "json_object"},
    }
    content = await _groq_content(http, payload)
    if not content:
        return complete_from_list(fallback, pool) or fallback, "heuristic"

    data = _parse_json_object(content) or {}
    chosen = strip_section_tails(_strip_index(str(data.get("title") or "")))
    if not chosen:
        start, end = data.get("start"), data.get("end")
        try:
            start_i, end_i = int(start), int(end)
            if band.lines and 1 <= start_i <= end_i <= len(band.lines):
                chosen = join_title_lines(band.lines[start_i - 1 : end_i])
        except (TypeError, ValueError):
            chosen = ""

    if not chosen:
        return complete_from_list(fallback, pool) or fallback, "heuristic"

    completed = complete_from_list(chosen, pool)
    if completed:
        return completed, "groq"

    recovered = recover_span(chosen, excerpt) or recover_span(chosen, " ".join(header_list))
    if recovered and grounded_in_sources(recovered, *sources):
        upgraded = complete_from_list(recovered, pool)
        return upgraded or recovered, "groq"

    if grounded_in_sources(chosen, *sources):
        return normalize(chosen), "groq"

    return complete_from_list(fallback, pool) or fallback, "heuristic"
