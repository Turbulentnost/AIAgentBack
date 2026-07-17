"""Кэш совещаний общего календаря calendar@ на выбранный слот (ручной перенос)."""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.logging import get_logger
from app.services.meeting_constants import SLOT_AVAILABILITY_CACHE_TTL_MINUTES

logger = get_logger(__name__)

_CACHE_TTL = timedelta(minutes=SLOT_AVAILABILITY_CACHE_TTL_MINUTES)
_lock = threading.Lock()
_entries: dict[str, tuple[datetime, CompanyCalendarSlotSnapshot]] = {}


@dataclass(frozen=True)
class CompanyCalendarSlotEvent:
    event_start: datetime
    event_end: datetime
    event_subject: str | None
    busy_type: str | None
    organizer: str | None
    event_attendees: tuple[str, ...]
    event_attendee_names: tuple[str, ...]


@dataclass(frozen=True)
class CompanyCalendarSlotSnapshot:
    calendar: str
    slot_start: datetime
    slot_end: datetime
    events: tuple[CompanyCalendarSlotEvent, ...]


def _normalize_instant(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def slots_match_snapshot(
    snapshot: CompanyCalendarSlotSnapshot,
    *,
    slot_start: datetime,
    slot_end: datetime,
) -> bool:
    return (
        _normalize_instant(snapshot.slot_start) == _normalize_instant(slot_start)
        and _normalize_instant(snapshot.slot_end) == _normalize_instant(slot_end)
    )


def store_company_calendar_snapshot(snapshot: CompanyCalendarSlotSnapshot) -> str:
    cache_id = str(uuid.uuid4())
    with _lock:
        _purge_expired_locked()
        _entries[cache_id] = (datetime.now(timezone.utc), snapshot)
    logger.info(
        "company_calendar_slot_cache.stored",
        cache_id=cache_id,
        calendar=snapshot.calendar,
        slot_start=snapshot.slot_start.isoformat(),
        slot_end=snapshot.slot_end.isoformat(),
        events=len(snapshot.events),
        ttl_minutes=SLOT_AVAILABILITY_CACHE_TTL_MINUTES,
    )
    return cache_id


def get_company_calendar_snapshot(cache_id: str | None) -> CompanyCalendarSlotSnapshot | None:
    if not cache_id or not str(cache_id).strip():
        return None
    with _lock:
        _purge_expired_locked()
        entry = _entries.get(str(cache_id).strip())
    if entry is None:
        return None
    return entry[1]


def _purge_expired_locked() -> None:
    now = datetime.now(timezone.utc)
    expired = [
        cache_id
        for cache_id, (stored_at, _) in _entries.items()
        if now - stored_at > _CACHE_TTL
    ]
    for cache_id in expired:
        _entries.pop(cache_id, None)


def company_calendar_event_snapshots_from_items(
    items: list[Any],
    *,
    slot_start: datetime,
    slot_end: datetime,
    config: Any,
) -> list[CompanyCalendarSlotEvent]:
    from app.tools.Outlook.cancel_meeting import to_local
    from app.tools.Outlook.slot_search.attendees import (
        calendar_item_attendee_display_names,
        calendar_item_attendee_emails,
    )
    from app.tools.Outlook.slot_search.busy import event_interval
    from app.tools.Outlook.slot_search.rules import intervals_overlap

    local_start = to_local(slot_start, config)
    local_end = to_local(slot_end, config)
    snapshots: list[CompanyCalendarSlotEvent] = []
    for item in items:
        interval = event_interval(item, config)
        if interval is None:
            continue
        event_start, event_end = interval
        if not intervals_overlap(local_start, local_end, event_start, event_end):
            continue
        organizer = None
        organizer_obj = getattr(item, "organizer", None)
        if organizer_obj is not None:
            organizer = getattr(organizer_obj, "email_address", None) or str(organizer_obj)
        snapshots.append(
            CompanyCalendarSlotEvent(
                event_start=event_start,
                event_end=event_end,
                event_subject=str(getattr(item, "subject", "") or "").strip() or None,
                busy_type=str(getattr(item, "legacy_free_busy_status", "") or "").strip() or None,
                organizer=str(organizer).strip() if organizer else None,
                event_attendees=tuple(calendar_item_attendee_emails(item)),
                event_attendee_names=tuple(calendar_item_attendee_display_names(item)),
            )
        )
    return snapshots
