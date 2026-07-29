"""Short-lived in-memory progress lines for supplier search / Qwen browse.

Keyed by operation_id (idempotency key). Soft-degrades: emit/get never raise
to callers. Used so the UI can poll real stages while the long sync search
holds an uncommitted DB transaction.
"""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Iterator
from urllib.parse import urlparse

_DEFAULT_TTL_SECONDS = 600.0
_MAX_LINES = 40

_current_operation_id: ContextVar[str | None] = ContextVar(
    "procurement_search_progress_op", default=None
)
_lock = threading.Lock()


@dataclass
class _ProgressEntry:
    case_id: str | None
    lines: list[str] = field(default_factory=list)
    updated_at: float = field(default_factory=time.monotonic)
    status: str = "running"


_STORE: dict[str, _ProgressEntry] = {}


def _purge_locked(now: float | None = None) -> None:
    ts = now if now is not None else time.monotonic()
    stale = [
        key
        for key, entry in _STORE.items()
        if ts - entry.updated_at > _DEFAULT_TTL_SECONDS
    ]
    for key in stale:
        _STORE.pop(key, None)


def begin_progress(operation_id: str, *, case_id: str | None = None) -> None:
    """Reset buffer for a new search run."""
    if not operation_id:
        return
    with _lock:
        _purge_locked()
        _STORE[operation_id] = _ProgressEntry(case_id=case_id, status="running")


def emit_progress(line: str, *, operation_id: str | None = None) -> None:
    """Append a short Russian progress line (no-op if no active key)."""
    text = (line or "").strip()
    if not text:
        return
    op = operation_id or _current_operation_id.get()
    if not op:
        return
    with _lock:
        _purge_locked()
        entry = _STORE.get(op)
        if entry is None:
            entry = _ProgressEntry(case_id=None, status="running")
            _STORE[op] = entry
        if entry.lines and entry.lines[-1] == text:
            entry.updated_at = time.monotonic()
            return
        entry.lines.append(text[:240])
        if len(entry.lines) > _MAX_LINES:
            entry.lines = entry.lines[-_MAX_LINES:]
        entry.updated_at = time.monotonic()


def finish_progress(
    operation_id: str | None = None,
    *,
    status: str = "completed",
) -> None:
    op = operation_id or _current_operation_id.get()
    if not op:
        return
    with _lock:
        entry = _STORE.get(op)
        if entry is None:
            return
        entry.status = status
        entry.updated_at = time.monotonic()


def get_progress(operation_id: str) -> list[str]:
    if not operation_id:
        return []
    with _lock:
        _purge_locked()
        entry = _STORE.get(operation_id)
        if entry is None:
            return []
        return list(entry.lines)


def get_progress_meta(operation_id: str) -> dict[str, object] | None:
    if not operation_id:
        return None
    with _lock:
        _purge_locked()
        entry = _STORE.get(operation_id)
        if entry is None:
            return None
        return {
            "operation_id": operation_id,
            "case_id": entry.case_id,
            "status": entry.status,
            "thoughts": list(entry.lines),
            "updated_at": entry.updated_at,
        }


def clear_progress(operation_id: str) -> None:
    if not operation_id:
        return
    with _lock:
        _STORE.pop(operation_id, None)


@contextmanager
def progress_scope(
    operation_id: str,
    *,
    case_id: str | None = None,
) -> Iterator[str]:
    """Bind operation_id for nested emit_progress() calls (asyncio-safe)."""
    begin_progress(operation_id, case_id=case_id)
    token = _current_operation_id.set(operation_id)
    try:
        yield operation_id
    finally:
        _current_operation_id.reset(token)


def progress_domain(url: str | None) -> str:
    """Host for «Открываю …» lines."""
    raw = (url or "").strip()
    if not raw:
        return "сайт"
    try:
        host = urlparse(raw).netloc or raw
    except Exception:
        host = raw
    host = host.removeprefix("www.")
    return (host or "сайт")[:80]


def truncate_query(value: str, max_len: int = 72) -> str:
    text = (value or "").strip()
    if len(text) <= max_len:
        return text
    return f"{text[: max_len - 1]}…"


__all__ = [
    "begin_progress",
    "clear_progress",
    "emit_progress",
    "finish_progress",
    "get_progress",
    "get_progress_meta",
    "progress_domain",
    "progress_scope",
    "truncate_query",
]
