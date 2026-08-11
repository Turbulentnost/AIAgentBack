"""Retry / concurrency helpers for flaky IMAP (SELECT Server Unavailable)."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from threading import Lock, Semaphore
from typing import TypeVar

import structlog

from agent_pochta.config import Settings, get_settings

logger = structlog.get_logger(__name__)

T = TypeVar("T")

_slot_lock = Lock()
_slot: Semaphore | None = None
_slot_limit: int | None = None


def is_transient_imap_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    needles = (
        "server unavailable",
        "select failed",
        "temporary failure",
        "try again",
        "temporarily unavailable",
        "connection reset",
        "connection aborted",
        "broken pipe",
        "timed out",
        "timeout",
        "eof occurred",
        "socket error",
        "bye",
    )
    return any(n in text for n in needles)


def _semaphore_for(settings: Settings) -> Semaphore:
    global _slot, _slot_limit
    limit = max(1, int(getattr(settings, "imap_max_concurrent", 2) or 2))
    with _slot_lock:
        if _slot is None or _slot_limit != limit:
            _slot = Semaphore(limit)
            _slot_limit = limit
        return _slot


@contextmanager
def imap_concurrency_slot(settings: Settings | None = None) -> Iterator[None]:
    """Ограничивает параллельные IMAP-сессии (poller + UI downloads)."""
    cfg = settings or get_settings()
    sem = _semaphore_for(cfg)
    sem.acquire()
    try:
        yield
    finally:
        sem.release()


def call_with_imap_retries(
    operation: Callable[[], T],
    *,
    settings: Settings | None = None,
    what: str = "imap_operation",
) -> T:
    """Повторяет операцию при transient IMAP-ошибках с коротким backoff."""
    cfg = settings or get_settings()
    retries = max(1, int(getattr(cfg, "imap_operation_retries", 3) or 3))
    delay = float(getattr(cfg, "imap_operation_retry_delay_sec", 1.5) or 1.5)
    last: BaseException | None = None
    for attempt in range(retries):
        try:
            return operation()
        except Exception as exc:
            last = exc
            if not is_transient_imap_error(exc) or attempt + 1 >= retries:
                raise
            sleep_for = delay * (attempt + 1)
            logger.warning(
                "imap_transient_retry",
                what=what,
                attempt=attempt + 1,
                retries=retries,
                sleep_sec=sleep_for,
                error=str(exc),
            )
            time.sleep(sleep_for)
    assert last is not None
    raise last
