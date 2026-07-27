from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from app.tools.Outlook.cancel_meeting import to_local
from app.tools.Outlook.outlook_config import OutlookConfig

from .busy import coalesce_intervals, fetch_all_busy_intervals, fetch_busy_intervals_freebusy_events
from .constants import AvailabilitySource
from .rules import intervals_overlap, slot_respects_rules
from .timing import timed_step


def is_free_for_attendee(
    start: datetime,
    duration: timedelta,
    busy_intervals: list[tuple[datetime, datetime]],
    config: OutlookConfig,
) -> bool:
    local_start = to_local(start, config)
    local_end = local_start + duration
    for busy_start, busy_end in busy_intervals:
        if intervals_overlap(
            local_start,
            local_end,
            to_local(busy_start, config),
            to_local(busy_end, config),
        ):
            return False
    return True

def is_free_for_all(
    start: datetime,
    duration: timedelta,
    busy_by_attendee: dict[str, list[tuple[datetime, datetime]]],
    config: OutlookConfig,
) -> bool:
    for intervals in busy_by_attendee.values():
        if not is_free_for_attendee(start, duration, intervals, config):
            return False
    return True

def partition_attendees_at_slot(
    slot_start: datetime,
    duration: timedelta,
    *,
    attendees: list[str],
    busy_by_attendee: dict[str, list[tuple[datetime, datetime]]],
    config: OutlookConfig,
) -> tuple[list[str], list[str]]:
    free: list[str] = []
    busy: list[str] = []
    for email in attendees:
        intervals = busy_by_attendee.get(email, [])
        if is_free_for_attendee(slot_start, duration, intervals, config):
            free.append(email)
        else:
            busy.append(email)
    return free, busy

def union_busy_for_all(
    busy_by_attendee: dict[str, list[tuple[datetime, datetime]]],
    config: OutlookConfig,
    range_start: datetime,
    range_end: datetime,
) -> list[tuple[datetime, datetime]]:
    """Объединение занятости всех участников: занято, если занят хотя бы один."""
    all_intervals: list[tuple[datetime, datetime]] = []
    for intervals in busy_by_attendee.values():
        all_intervals.extend(intervals)
    return coalesce_intervals(
        all_intervals,
        config,
        clip_start=range_start,
        clip_end=range_end,
    )

def verify_slot_with_calendar(
    *,
    config: OutlookConfig,
    attendees: list[str],
    slot_start: datetime,
    duration: timedelta,
    max_items: int,
    workers: int,
) -> tuple[bool, dict[str, list[tuple[datetime, datetime]]]]:
    del max_items, workers
    window_start = slot_start - timedelta(hours=2)
    window_end = slot_start + duration + timedelta(hours=2)
    with timed_step("verify.freebusy_events", attendees=len(attendees)):
        busy_by_attendee = fetch_busy_intervals_freebusy_events(
            config,
            attendees,
            window_start,
            window_end,
        )
    return is_free_for_all(slot_start, duration, busy_by_attendee, config), busy_by_attendee

