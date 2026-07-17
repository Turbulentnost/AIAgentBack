from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from app.tools.Outlook.cancel_meeting import to_local
from app.tools.Outlook.outlook_config import OutlookConfig
from app.tools.Outlook.read_calendars import read_calendar_items_in_range

from .attendees import (
    _human_attendees_for_reschedule_hint,
    calendar_item_attendee_display_names,
    calendar_item_attendee_emails,
    normalize_calendar_email,
    participant_involved_in_calendar_item,
)
from .availability import is_free_for_all, is_free_for_attendee
from .busy import (
    coalesce_intervals,
    event_interval,
    fetch_busy_intervals_freebusy,
    fetch_busy_intervals_freebusy_events,
    fetch_freebusy_calendar_events,
    freebusy_event_interval,
)
from .constants import LOW_MOVABILITY_SUBJECT_KEYWORDS
from .iteration import iterate_slot_candidates
from .rules import advance_candidate, intervals_overlap, slot_respects_rules
from .timing import logger, timed_step


def movability_score(*, busy_type: str, subject: str) -> str:
    subject_lower = subject.lower()
    if any(keyword in subject_lower for keyword in LOW_MOVABILITY_SUBJECT_KEYWORDS):
        return "low"
    status = busy_type.strip()
    if status == "OOF":
        return "low"
    if status == "Tentative":
        return "high"
    if status in {"Busy", "WorkingElsewhere"}:
        return "medium"
    return "medium"

def conflicting_events_at_slot(
    events: list[Any],
    slot_start: datetime,
    duration: timedelta,
    config: OutlookConfig,
) -> list[dict[str, Any]]:
    local_start = to_local(slot_start, config)
    local_end = local_start + duration
    records: list[dict[str, Any]] = []
    for event in events:
        interval = freebusy_event_interval(event, config)
        if interval is None:
            continue
        event_start, event_end = interval
        if not intervals_overlap(local_start, local_end, event_start, event_end):
            continue
        subject = str(getattr(event, "subject", "") or "").strip()
        busy_type = str(getattr(event, "busy_type", "") or "").strip()
        records.append(
            {
                "event_start": event_start.isoformat(),
                "event_end": event_end.isoformat(),
                "event_subject": subject or None,
                "busy_type": busy_type or None,
                "movability": movability_score(busy_type=busy_type, subject=subject),
            }
        )
    return records

def conflicting_intervals_at_slot(
    busy_intervals: list[tuple[datetime, datetime]],
    slot_start: datetime,
    duration: timedelta,
    config: OutlookConfig,
) -> list[dict[str, Any]]:
    local_start = to_local(slot_start, config)
    local_end = local_start + duration
    records: list[dict[str, Any]] = []
    for busy_start, busy_end in busy_intervals:
        busy_start_local = to_local(busy_start, config)
        busy_end_local = to_local(busy_end, config)
        if not intervals_overlap(local_start, local_end, busy_start_local, busy_end_local):
            continue
        overlap_start = max(local_start, busy_start_local)
        overlap_end = min(local_end, busy_end_local)
        records.append(
            {
                "event_start": overlap_start.isoformat(),
                "event_end": overlap_end.isoformat(),
                "event_subject": None,
                "busy_type": "Busy",
                "movability": "medium",
            }
        )
    return records

def _apply_blocked_slots_to_busy(
    busy_by_attendee: dict[str, list[tuple[datetime, datetime]]],
    *,
    owner_email: str | None,
    blocked_slots: list[tuple[datetime, datetime]],
    config: OutlookConfig,
) -> dict[str, list[tuple[datetime, datetime]]]:
    if not blocked_slots or not owner_email:
        return busy_by_attendee
    updated = dict(busy_by_attendee)
    owner_key = owner_email.strip().lower()
    existing = list(updated.get(owner_key, []))
    updated[owner_key] = coalesce_intervals(existing + blocked_slots, config)
    return updated

def _apply_reserved_slot_to_busy(
    busy_by_attendee: dict[str, list[tuple[datetime, datetime]]],
    *,
    owner_email: str | None,
    reserved_slot: tuple[datetime, datetime] | None,
    config: OutlookConfig,
) -> dict[str, list[tuple[datetime, datetime]]]:
    if reserved_slot is None:
        return busy_by_attendee
    return _apply_blocked_slots_to_busy(
        busy_by_attendee,
        owner_email=owner_email,
        blocked_slots=[reserved_slot],
        config=config,
    )

def _exclude_window_from_busy(
    busy_by_attendee: dict[str, list[tuple[datetime, datetime]]],
    *,
    exclude_start: datetime,
    exclude_end: datetime,
    config: OutlookConfig,
) -> dict[str, list[tuple[datetime, datetime]]]:
    """Убирает переносимую встречу из занятости — ищем окно для её переноса."""
    local_exclude_start = to_local(exclude_start, config)
    local_exclude_end = to_local(exclude_end, config)
    updated: dict[str, list[tuple[datetime, datetime]]] = {}
    for email, intervals in busy_by_attendee.items():
        pruned: list[tuple[datetime, datetime]] = []
        for busy_start, busy_end in intervals:
            busy_start_local = to_local(busy_start, config)
            busy_end_local = to_local(busy_end, config)
            if busy_end_local <= local_exclude_start or busy_start_local >= local_exclude_end:
                pruned.append((busy_start_local, busy_end_local))
                continue
            if busy_start_local < local_exclude_start:
                pruned.append((busy_start_local, local_exclude_start))
            if busy_end_local > local_exclude_end:
                pruned.append((local_exclude_end, busy_end_local))
        updated[email] = coalesce_intervals(pruned, config)
    return updated


def _fetch_group_busy_for_hint(
    config: OutlookConfig,
    attendees: list[str],
    fetch_start: datetime,
    fetch_end: datetime,
) -> dict[str, list[tuple[datetime, datetime]]]:
    try:
        busy_by_attendee = fetch_busy_intervals_freebusy_events(
            config,
            attendees,
            fetch_start,
            fetch_end,
        )
        if any(intervals for intervals in busy_by_attendee.values()):
            return busy_by_attendee
    except Exception as exc:
        logger.warning(
            "reschedule_hint_group_events_fetch_failed attendees=%s error=%s",
            len(attendees),
            exc,
        )
    return fetch_busy_intervals_freebusy(
        config,
        attendees,
        fetch_start,
        fetch_end,
    )


def suggest_reschedule_window(
    *,
    event_start: datetime,
    event_end: datetime,
    busy_intervals: list[tuple[datetime, datetime]],
    config: OutlookConfig,
    step: timedelta,
    search_end: datetime,
    reserved_slot: tuple[datetime, datetime] | None = None,
    blocked_hint_slots: list[tuple[datetime, datetime]] | None = None,
    owner_email: str | None = None,
    meeting_attendees: list[str] | None = None,
    skip_group_busy_fetch: bool = False,
) -> tuple[datetime, datetime] | None:
    event_start = to_local(event_start, config)
    event_end = to_local(event_end, config)
    search_end = to_local(search_end, config)
    blocked_slots: list[tuple[datetime, datetime]] = []
    if reserved_slot is not None:
        blocked_slots.append(
            (
                to_local(reserved_slot[0], config),
                to_local(reserved_slot[1], config),
            )
        )
    for slot_start, slot_end in blocked_hint_slots or []:
        blocked_slots.append(
            (
                to_local(slot_start, config),
                to_local(slot_end, config),
            )
        )
    blocked_slots = coalesce_intervals(blocked_slots, config)

    duration = event_end - event_start
    if duration <= timedelta(0):
        duration = timedelta(minutes=30)

    attendee_emails = [
        item.strip().lower()
        for item in (meeting_attendees or [])
        if isinstance(item, str) and item.strip()
    ]
    attendee_emails = list(dict.fromkeys(attendee_emails))
    owner = (owner_email or "").strip().lower() or None
    if owner and owner not in attendee_emails:
        attendee_emails.insert(0, owner)

    group_attendees = _human_attendees_for_reschedule_hint(attendee_emails)
    use_group_check = len(group_attendees) >= 2 and not skip_group_busy_fetch
    busy_by_attendee: dict[str, list[tuple[datetime, datetime]]] | None = None
    if use_group_check:
        fetch_start = event_end
        fetch_end = search_end
        try:
            busy_by_attendee = _fetch_group_busy_for_hint(
                config,
                group_attendees,
                fetch_start,
                fetch_end,
            )
        except Exception as exc:
            logger.warning(
                "reschedule_hint_group_busy_fetch_failed attendees=%s error=%s",
                len(group_attendees),
                exc,
            )
            busy_by_attendee = None
            use_group_check = False

    if use_group_check and busy_by_attendee is not None:
        busy_by_attendee = _exclude_window_from_busy(
            busy_by_attendee,
            exclude_start=event_start,
            exclude_end=event_end,
            config=config,
        )
        busy_by_attendee = _apply_blocked_slots_to_busy(
            busy_by_attendee,
            owner_email=owner,
            blocked_slots=blocked_slots,
            config=config,
        )
        candidate = advance_candidate(event_end, step, duration, config)
        while candidate < search_end:
            if slot_respects_rules(candidate, duration, config) and is_free_for_all(
                candidate,
                duration,
                busy_by_attendee,
                config,
            ):
                return candidate, candidate + duration
            candidate = advance_candidate(candidate, step, duration, config)

    effective_busy = coalesce_intervals(
        list(busy_intervals) + blocked_slots,
        config,
    )
    candidate = advance_candidate(event_end, step, duration, config)
    while candidate < search_end:
        if slot_respects_rules(candidate, duration, config) and is_free_for_attendee(
            candidate,
            duration,
            effective_busy,
            config,
        ):
            return candidate, candidate + duration
        candidate = advance_candidate(candidate, step, duration, config)
    del event_start
    return None

def build_conflict_records(
    *,
    email: str,
    slot_start: datetime,
    duration: timedelta,
    busy_intervals: list[tuple[datetime, datetime]],
    calendar_events: list[Any],
    config: OutlookConfig,
    step: timedelta,
    search_end: datetime,
    max_calendar_items: int = 50,
) -> list[dict[str, Any]]:
    del max_calendar_items
    freebusy_records = conflicting_events_at_slot(calendar_events, slot_start, duration, config)
    for record in freebusy_records:
        record["source"] = "freebusy"

    interval_records: list[dict[str, Any]] = []
    if not freebusy_records:
        interval_records = conflicting_intervals_at_slot(
            busy_intervals,
            slot_start,
            duration,
            config,
        )
        for record in interval_records:
            record["source"] = "interval"

    merged_records = dedupe_conflict_records(freebusy_records + interval_records)
    reserved_slot = (slot_start, slot_start + duration)
    conflicts: list[dict[str, Any]] = []
    assigned_hints: list[tuple[datetime, datetime]] = []
    for record in merged_records:
        event_start = datetime.fromisoformat(record["event_start"])
        event_end = datetime.fromisoformat(record["event_end"])
        hint = suggest_reschedule_window(
            event_start=event_start,
            event_end=event_end,
            busy_intervals=busy_intervals,
            config=config,
            step=step,
            search_end=search_end,
            reserved_slot=reserved_slot,
            blocked_hint_slots=assigned_hints,
            owner_email=email,
            meeting_attendees=record.get("event_attendees"),
        )
        if hint is not None:
            assigned_hints.append(hint)
        meeting_attendees = list(record.get("event_attendees") or [])
        if email.strip().lower() not in {item.lower() for item in meeting_attendees}:
            meeting_attendees.insert(0, email.strip().lower())
        subject = str(record.get("event_subject") or "")
        busy_type = str(record.get("busy_type") or "")
        source = record.get("source") or "interval"
        if source not in {"calendar", "freebusy", "interval", "company_calendar"}:
            source = "interval"
        conflicts.append(
            {
                "email": email,
                "event_start": record["event_start"],
                "event_end": record["event_end"],
                "event_subject": record.get("event_subject"),
                "busy_type": record.get("busy_type"),
                "movability": record.get("movability") or "medium",
                "movability_reason": movability_reason(
                    busy_type=busy_type,
                    subject=subject,
                    source=source,
                ),
                "source": source,
                "event_attendees": meeting_attendees,
                "can_auto_reschedule": False,
                "reschedule_hint_start": hint[0].isoformat() if hint else None,
                "reschedule_hint_end": hint[1].isoformat() if hint else None,
            }
        )
    return conflicts

def movability_reason(
    *,
    busy_type: str,
    subject: str,
    source: Literal["calendar", "freebusy", "interval", "company_calendar"],
) -> str:
    subject_lower = subject.lower()
    if any(keyword in subject_lower for keyword in LOW_MOVABILITY_SUBJECT_KEYWORDS):
        return "protected_subject"
    status = busy_type.strip()
    if status == "OOF":
        return "oof"
    if status == "Tentative":
        return "tentative"
    if source == "interval":
        return "unknown_interval"
    if status in {"Busy", "WorkingElsewhere"}:
        return "busy"
    return "busy"

def conflicting_calendar_items_at_slot(
    items: list[Any],
    slot_start: datetime,
    duration: timedelta,
    config: OutlookConfig,
) -> list[dict[str, Any]]:
    local_start = to_local(slot_start, config)
    local_end = local_start + duration
    records: list[dict[str, Any]] = []
    for item in items:
        interval = event_interval(item, config)
        if interval is None:
            continue
        event_start, event_end = interval
        if not intervals_overlap(local_start, local_end, event_start, event_end):
            continue
        subject = str(getattr(item, "subject", "") or "").strip()
        busy_type = str(getattr(item, "legacy_free_busy_status", "") or "").strip()
        organizer = None
        organizer_obj = getattr(item, "organizer", None)
        if organizer_obj is not None:
            organizer = getattr(organizer_obj, "email_address", None) or str(organizer_obj)
        records.append(
            {
                "event_start": event_start.isoformat(),
                "event_end": event_end.isoformat(),
                "event_subject": subject or None,
                "busy_type": busy_type or None,
                "organizer": organizer,
                "event_attendees": calendar_item_attendee_emails(item),
                "event_attendee_names": calendar_item_attendee_display_names(item),
                "movability": movability_score(busy_type=busy_type, subject=subject),
                "source": "calendar",
            }
        )
    return records


def hydrate_company_calendar_items_for_slot(
    items: list[Any],
    *,
    slot_start: datetime,
    slot_end: datetime,
    config: OutlookConfig,
) -> int:
    """Догружает участников только у событий общего календаря, пересекающих слот."""
    from app.tools.Outlook.read_calendars import hydrate_calendar_item_attendees

    local_start = to_local(slot_start, config)
    local_end = to_local(slot_end, config)
    overlapping: list[Any] = []
    for item in items:
        interval = event_interval(item, config)
        if interval is None:
            continue
        event_start, event_end = interval
        if intervals_overlap(local_start, local_end, event_start, event_end):
            overlapping.append(item)
    if not overlapping:
        return 0
    hydrate_calendar_item_attendees(overlapping)
    return len(overlapping)


def conflicting_company_calendar_items_at_slot(
    items: list[Any],
    slot_start: datetime,
    duration: timedelta,
    config: OutlookConfig,
    *,
    attendee_email: str,
    attendee_fio: str | None = None,
) -> list[dict[str, Any]]:
    """Конфликты из общего календаря компании для конкретного участника."""
    normalized_email = attendee_email.strip().lower()
    if not normalized_email:
        return []

    local_start = to_local(slot_start, config)
    local_end = local_start + duration
    records: list[dict[str, Any]] = []
    for item in items:
        interval = event_interval(item, config)
        if interval is None:
            continue
        event_start, event_end = interval
        if not intervals_overlap(local_start, local_end, event_start, event_end):
            continue

        if not participant_involved_in_calendar_item(
            item,
            attendee_email=normalized_email,
            attendee_fio=attendee_fio,
        ):
            continue

        subject = str(getattr(item, "subject", "") or "").strip()
        busy_type = str(getattr(item, "legacy_free_busy_status", "") or "").strip()
        organizer = None
        organizer_obj = getattr(item, "organizer", None)
        if organizer_obj is not None:
            organizer = getattr(organizer_obj, "email_address", None) or str(organizer_obj)
        records.append(
            {
                "event_start": event_start.isoformat(),
                "event_end": event_end.isoformat(),
                "event_subject": subject or None,
                "busy_type": busy_type or None,
                "organizer": organizer,
                "event_attendees": calendar_item_attendee_emails(item),
                "event_attendee_names": calendar_item_attendee_display_names(item),
                "movability": movability_score(busy_type=busy_type, subject=subject),
                "movability_reason": movability_reason(
                    busy_type=busy_type,
                    subject=subject,
                    source="company_calendar",
                ),
                "source": "company_calendar",
            }
        )
    return records

def dedupe_conflict_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Один интервал — одна запись; при дубле оставляем запись с темой (calendar > company > freebusy > interval)."""
    source_rank = {"calendar": 0, "company_calendar": 1, "freebusy": 2, "interval": 3}
    by_interval: dict[tuple[str, str], dict[str, Any]] = {}
    order: list[tuple[str, str]] = []

    def should_replace(existing: dict[str, Any], candidate: dict[str, Any]) -> bool:
        new_subject = str(candidate.get("event_subject") or "").strip()
        old_subject = str(existing.get("event_subject") or "").strip()
        if new_subject and not old_subject:
            return True
        if old_subject and not new_subject:
            return False
        new_rank = source_rank.get(str(candidate.get("source") or "interval"), 2)
        old_rank = source_rank.get(str(existing.get("source") or "interval"), 2)
        return new_rank < old_rank

    for record in records:
        key = (
            str(record.get("event_start") or ""),
            str(record.get("event_end") or ""),
        )
        if not key[0] or not key[1]:
            continue
        if key not in by_interval:
            order.append(key)
            by_interval[key] = record
            continue
        if should_replace(by_interval[key], record):
            by_interval[key] = record
        elif record.get("event_attendees") and not by_interval[key].get("event_attendees"):
            by_interval[key]["event_attendees"] = record["event_attendees"]
        record_names = record.get("event_attendee_names")
        if record_names and not by_interval[key].get("event_attendee_names"):
            by_interval[key]["event_attendee_names"] = record_names

    return [by_interval[key] for key in order]

def _skip_group_busy_fetch_for_hint(
    *,
    light_hints: bool,
    meeting_attendees: list[Any] | None,
) -> bool:
    """Лёгкий режим только для одиночных встреч; для совещаний нужен group freebusy."""
    group = _human_attendees_for_reschedule_hint(
        [
            email
            for email in (meeting_attendees or [])
            if isinstance(email, str) and email.strip()
        ]
    )
    return light_hints and len(group) < 2


def _busy_intervals_for_hint(
    *,
    owner_email: str,
    event_end: datetime,
    search_end: datetime,
    busy_intervals: list[tuple[datetime, datetime]],
    config: OutlookConfig,
    use_cached_only: bool,
) -> list[tuple[datetime, datetime]]:
    if use_cached_only:
        return busy_intervals
    normalized_owner = owner_email.strip().lower()
    if not normalized_owner:
        return busy_intervals
    try:
        fetched = fetch_busy_intervals_freebusy(
            config,
            [normalized_owner],
            event_end,
            search_end,
        )
        return fetched.get(normalized_owner, busy_intervals)
    except Exception as exc:
        logger.warning(
            "reschedule_hint_owner_busy_fetch_failed email=%s error=%s",
            normalized_owner,
            exc,
        )
        return busy_intervals


def attach_reschedule_hints(
    records: list[dict[str, Any]],
    *,
    owner_email: str,
    busy_intervals: list[tuple[datetime, datetime]],
    config: OutlookConfig,
    step: timedelta,
    search_end: datetime,
    reserved_slot: tuple[datetime, datetime] | None = None,
    light_hints: bool = False,
) -> list[dict[str, Any]]:
    conflicts: list[dict[str, Any]] = []
    assigned_hints: list[tuple[datetime, datetime]] = []
    hints_by_event: dict[tuple[str, str, str], tuple[datetime, datetime] | None] = {}

    def _event_key(record: dict[str, Any]) -> tuple[str, str, str]:
        return (
            str(record["event_start"]),
            str(record["event_end"]),
            str(record.get("event_subject") or ""),
        )

    def _resolve_hint(
        *,
        event_start: datetime,
        event_end: datetime,
        meeting_attendees: list[Any] | None,
        skip_group_busy: bool,
        hint_busy_intervals: list[tuple[datetime, datetime]],
    ) -> tuple[datetime, datetime] | None:
        hint = suggest_reschedule_window(
            event_start=event_start,
            event_end=event_end,
            busy_intervals=hint_busy_intervals,
            config=config,
            step=step,
            search_end=search_end,
            reserved_slot=reserved_slot,
            blocked_hint_slots=assigned_hints,
            owner_email=owner_email,
            meeting_attendees=meeting_attendees,
            skip_group_busy_fetch=skip_group_busy,
        )
        if hint is not None or skip_group_busy:
            return hint
        return suggest_reschedule_window(
            event_start=event_start,
            event_end=event_end,
            busy_intervals=hint_busy_intervals,
            config=config,
            step=step,
            search_end=search_end,
            reserved_slot=reserved_slot,
            blocked_hint_slots=assigned_hints,
            owner_email=owner_email,
            meeting_attendees=[owner_email],
            skip_group_busy_fetch=True,
        )

    for record in records:
        event_start = datetime.fromisoformat(record["event_start"])
        event_end = datetime.fromisoformat(record["event_end"])
        meeting_attendees = record.get("event_attendees")
        event_key = _event_key(record)
        if event_key in hints_by_event:
            hint = hints_by_event[event_key]
        else:
            skip_group_busy = _skip_group_busy_fetch_for_hint(
                light_hints=light_hints,
                meeting_attendees=meeting_attendees,
            )
            hint_busy_intervals = _busy_intervals_for_hint(
                owner_email=owner_email,
                event_end=event_end,
                search_end=search_end,
                busy_intervals=busy_intervals,
                config=config,
                use_cached_only=not skip_group_busy,
            )
            hint = _resolve_hint(
                event_start=event_start,
                event_end=event_end,
                meeting_attendees=meeting_attendees,
                skip_group_busy=skip_group_busy,
                hint_busy_intervals=hint_busy_intervals,
            )
            hints_by_event[event_key] = hint
        if hint is not None:
            assigned_hints.append(hint)
        subject = str(record.get("event_subject") or "")
        busy_type = str(record.get("busy_type") or "")
        source = record.get("source") or "interval"
        if source not in {"calendar", "freebusy", "interval", "company_calendar"}:
            source = "interval"
        conflicts.append(
            {
                **record,
                "movability": record.get("movability") or "medium",
                "movability_reason": movability_reason(
                    busy_type=busy_type,
                    subject=subject,
                    source=source,
                ),
                "can_auto_reschedule": False,
                "reschedule_hint_start": hint[0].isoformat() if hint else None,
                "reschedule_hint_end": hint[1].isoformat() if hint else None,
            }
        )
    return conflicts

