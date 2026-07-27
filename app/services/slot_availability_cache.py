"""Кэш занятости из подбора слота для повторного использования при ручной проверке."""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.logging import get_logger
from app.services.meeting_constants import (
    SLOT_AVAILABILITY_CACHE_TTL_MINUTES,
    SLOT_AVAILABILITY_CACHE_WINDOW_DAYS,
)

logger = get_logger(__name__)

_CACHE_TTL = timedelta(minutes=SLOT_AVAILABILITY_CACHE_TTL_MINUTES)
_lock = threading.Lock()
_entries: dict[str, tuple[datetime, SlotAvailabilitySnapshot]] = {}


@dataclass(frozen=True)
class SlotAvailabilitySnapshot:
    memo_ref_key: str
    attendee_emails: tuple[str, ...]
    window_start: datetime
    window_end: datetime
    busy_by_attendee: dict[str, list[tuple[datetime, datetime]]]


def serialize_busy_snapshot(
    *,
    memo_ref_key: str,
    attendee_emails: list[str],
    window_start: datetime,
    window_end: datetime,
    busy_by_attendee: dict[str, list[tuple[datetime, datetime]]],
) -> dict[str, Any]:
    return {
        "memo_ref_key": memo_ref_key.strip().lower(),
        "attendee_emails": [email.strip().lower() for email in attendee_emails if email.strip()],
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
        "busy_by_attendee": {
            email: [
                {"start": start.isoformat(), "end": end.isoformat()}
                for start, end in intervals
            ]
            for email, intervals in busy_by_attendee.items()
        },
    }


def snapshot_from_payload(payload: dict[str, Any]) -> SlotAvailabilitySnapshot | None:
    raw_start = payload.get("window_start")
    raw_end = payload.get("window_end")
    if not raw_start or not raw_end:
        return None
    try:
        window_start = datetime.fromisoformat(str(raw_start))
        window_end = datetime.fromisoformat(str(raw_end))
    except ValueError:
        return None

    busy_by_attendee: dict[str, list[tuple[datetime, datetime]]] = {}
    raw_busy = payload.get("busy_by_attendee") or {}
    if isinstance(raw_busy, dict):
        for email, intervals in raw_busy.items():
            parsed: list[tuple[datetime, datetime]] = []
            if not isinstance(intervals, list):
                continue
            for item in intervals:
                if not isinstance(item, dict):
                    continue
                start_raw = item.get("start")
                end_raw = item.get("end")
                if not start_raw or not end_raw:
                    continue
                try:
                    parsed.append(
                        (
                            datetime.fromisoformat(str(start_raw)),
                            datetime.fromisoformat(str(end_raw)),
                        )
                    )
                except ValueError:
                    continue
            busy_by_attendee[str(email).strip().lower()] = parsed

    emails = payload.get("attendee_emails") or []
    if not isinstance(emails, list):
        emails = []
    memo_ref_key = str(payload.get("memo_ref_key") or "").strip().lower()
    if not memo_ref_key:
        return None

    return SlotAvailabilitySnapshot(
        memo_ref_key=memo_ref_key,
        attendee_emails=tuple(
            str(email).strip().lower() for email in emails if str(email).strip()
        ),
        window_start=window_start,
        window_end=window_end,
        busy_by_attendee=busy_by_attendee,
    )


def trim_snapshot_for_cache(
    payload: dict[str, Any],
    *,
    max_window_days: int = SLOT_AVAILABILITY_CACHE_WINDOW_DAYS,
) -> dict[str, Any]:
    """Сужает снимок до окна переиспользования (не весь 30-дневный поиск)."""
    snapshot = snapshot_from_payload(payload)
    if snapshot is None:
        return payload

    capped_end = snapshot.window_start + timedelta(days=max(1, max_window_days))
    if snapshot.window_end <= capped_end:
        return payload

    trimmed_busy: dict[str, list[tuple[datetime, datetime]]] = {}
    for email, intervals in snapshot.busy_by_attendee.items():
        trimmed_busy[email] = [
            (start, end)
            for start, end in intervals
            if start < capped_end and end > snapshot.window_start
        ]

    return serialize_busy_snapshot(
        memo_ref_key=snapshot.memo_ref_key,
        attendee_emails=list(snapshot.attendee_emails),
        window_start=snapshot.window_start,
        window_end=capped_end,
        busy_by_attendee=trimmed_busy,
    )


def store_availability_snapshot(payload: dict[str, Any]) -> str | None:
    snapshot = snapshot_from_payload(payload)
    if snapshot is None:
        return None
    cache_id = str(uuid.uuid4())
    with _lock:
        _purge_expired_locked()
        _entries[cache_id] = (datetime.now(timezone.utc), snapshot)
    logger.info(
        "slot_availability_cache.stored",
        cache_id=cache_id,
        memo_ref_key=snapshot.memo_ref_key,
        attendees=len(snapshot.attendee_emails),
        window_start=snapshot.window_start.isoformat(),
        window_end=snapshot.window_end.isoformat(),
        ttl_minutes=SLOT_AVAILABILITY_CACHE_TTL_MINUTES,
    )
    return cache_id


def get_availability_snapshot(cache_id: str | None) -> SlotAvailabilitySnapshot | None:
    if not cache_id or not str(cache_id).strip():
        return None
    with _lock:
        _purge_expired_locked()
        entry = _entries.get(str(cache_id).strip())
    if entry is None:
        return None
    return entry[1]


def slot_within_snapshot_window(
    snapshot: SlotAvailabilitySnapshot,
    *,
    slot_start: datetime,
    slot_end: datetime,
) -> bool:
    return snapshot.window_start <= slot_start and slot_end <= snapshot.window_end


def _purge_expired_locked() -> None:
    now = datetime.now(timezone.utc)
    expired = [
        cache_id
        for cache_id, (stored_at, _) in _entries.items()
        if now - stored_at > _CACHE_TTL
    ]
    for cache_id in expired:
        _entries.pop(cache_id, None)
