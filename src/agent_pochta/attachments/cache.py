"""In-memory кэш байтов вложений (on-demand IMAP → UI)."""

from __future__ import annotations

import time
from dataclasses import dataclass
from threading import Lock

from agent_pochta.config import get_settings


@dataclass(frozen=True)
class CachedAttachment:
    content: bytes
    mime_type: str
    filename: str


_lock = Lock()
_store: dict[str, tuple[float, CachedAttachment]] = {}
_bytes_total = 0


def attachment_cache_key(mailbox: str, message_id: str, index: int, filename: str) -> str:
    base = (message_id or "").split("#", 1)[0].strip()
    return f"{mailbox.lower()}|{base}|{index}|{filename}"


def full_email_cache_key(mailbox: str, message_id: str) -> str:
    """Ключ in-memory кэша для полного RFC822 (.eml) письма."""
    base = (message_id or "").split("#", 1)[0].strip()
    return f"{mailbox.lower()}|{base}|__full_eml__"


def get_cached_attachment(key: str) -> CachedAttachment | None:
    settings = get_settings()
    ttl = max(0, int(settings.attachment_cache_ttl_sec))
    if ttl <= 0:
        return None
    now = time.monotonic()
    with _lock:
        entry = _store.get(key)
        if not entry:
            return None
        expires_at, cached = entry
        if now >= expires_at:
            _remove_locked(key)
            return None
        return cached


def put_cached_attachment(key: str, *, content: bytes, mime_type: str, filename: str) -> None:
    settings = get_settings()
    ttl = max(0, int(settings.attachment_cache_ttl_sec))
    if ttl <= 0 or not content:
        return
    max_bytes = max(1, int(settings.attachment_cache_max_mb)) * 1024 * 1024
    expires_at = time.monotonic() + ttl
    cached = CachedAttachment(content=content, mime_type=mime_type, filename=filename)
    with _lock:
        global _bytes_total
        if key in _store:
            _bytes_total -= len(_store[key][1].content)
        while _store and _bytes_total + len(content) > max_bytes:
            oldest_key = next(iter(_store))
            _remove_locked(oldest_key)
        _store[key] = (expires_at, cached)
        _bytes_total += len(content)


def clear_attachment_cache() -> None:
    with _lock:
        _store.clear()
        global _bytes_total
        _bytes_total = 0


def _remove_locked(key: str) -> None:
    global _bytes_total
    entry = _store.pop(key, None)
    if entry:
        _bytes_total -= len(entry[1].content)
