"""Bounded worker pool for PDF parsing, rendering and OCR.

Extraction is CPU and memory heavy: PyMuPDF parsing, a 2x page render and a
Tesseract subprocess. Left to the default asyncio executor, a burst of uploads
would start `min(32, cpu + 4)` of those per process and thrash the container.

Every request goes through one pool with a fixed number of slots. Requests that
cannot get a slot in time are refused with 503 + Retry-After instead of piling
up until the container runs out of memory.
"""

from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Optional, TypeVar

from .config import get_settings

logger = logging.getLogger("engine.pool")

T = TypeVar("T")


class EngineBusyError(RuntimeError):
    """No worker slot became free within the queue timeout."""


_executor: Optional[ThreadPoolExecutor] = None
_slots: Optional[asyncio.Semaphore] = None
_queue_timeout: float = 20.0


def start_pool() -> tuple[int, float]:
    """Create the pool. Called once per worker process from the app lifespan."""
    global _executor, _slots, _queue_timeout
    settings = get_settings()
    workers = settings.extract_workers
    _queue_timeout = settings.extract_queue_timeout
    if _executor is None:
        _executor = ThreadPoolExecutor(
            max_workers=workers,
            thread_name_prefix="extract",
        )
    _slots = asyncio.Semaphore(workers)
    return workers, _queue_timeout


def stop_pool() -> None:
    global _executor, _slots
    if _executor is not None:
        _executor.shutdown(wait=False, cancel_futures=True)
        _executor = None
    _slots = None


def pool_stats() -> dict[str, Any]:
    settings = get_settings()
    return {
        "extract_workers": settings.extract_workers,
        "queue_timeout_s": settings.extract_queue_timeout,
    }


async def run_extraction(func: Callable[..., T], *args: Any) -> T:
    """Run blocking extraction work on the pool, shedding load when saturated."""
    if _executor is None or _slots is None:
        # No lifespan ran (unit test or direct import): stay correct, just serial.
        return await asyncio.to_thread(func, *args)

    try:
        await asyncio.wait_for(_slots.acquire(), timeout=_queue_timeout)
    except asyncio.TimeoutError as exc:
        logger.warning("extraction queue full for %.1fs; shedding request", _queue_timeout)
        raise EngineBusyError("Engine is busy") from exc

    loop = asyncio.get_running_loop()
    try:
        return await loop.run_in_executor(_executor, func, *args)
    finally:
        _slots.release()


__all__ = [
    "EngineBusyError",
    "pool_stats",
    "run_extraction",
    "start_pool",
    "stop_pool",
]
