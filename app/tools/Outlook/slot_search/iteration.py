from __future__ import annotations

from datetime import datetime, timedelta
from typing import Iterator

from app.tools.Outlook.cancel_meeting import to_local
from app.tools.Outlook.outlook_config import OutlookConfig

from .busy import coalesce_intervals, merge_busy_intervals
from .constants import WORK_START
from .rules import (
    advance_candidate,
    align_preferred,
    combine,
    is_workday,
    not_before_now,
    slot_respects_rules,
    snap_to_step,
)


def iterate_slot_candidates(
    earliest_allowed: datetime,
    search_end: datetime,
    *,
    duration: timedelta,
    step: timedelta,
    config: OutlookConfig,
) -> Iterator[datetime]:
    candidate = max(earliest_allowed, align_preferred(earliest_allowed, config))
    safety_limit = max(
        1000,
        int((search_end - earliest_allowed).total_seconds() // max(step.total_seconds(), 60)) + 50,
    )
    emitted = 0
    while candidate < search_end and emitted < safety_limit:
        if slot_respects_rules(candidate, duration, config):
            yield candidate
            emitted += 1
        candidate = advance_candidate(candidate, step, duration, config)

def quorum_search_start(preferred: datetime, config: OutlookConfig) -> datetime:
    """Начало перебора: с 08:00 дня желаемой даты, а не с preferred (10:00 пропускает 09:00–12:00)."""
    requested = to_local(preferred, config).replace(second=0, microsecond=0)
    if not is_workday(requested, config):
        return max(align_preferred(requested, config), not_before_now(config))
    return max(combine(requested, WORK_START, config), not_before_now(config))

def first_valid_slot_in_window(
    window_start: datetime,
    window_end: datetime,
    *,
    duration: timedelta,
    step: timedelta,
    config: OutlookConfig,
) -> tuple[datetime | None, int]:
    """Первый слот в свободном окне [window_start, window_end)."""
    local_start = to_local(window_start, config)
    local_end = to_local(window_end, config)
    if local_end <= local_start:
        return None, 0

    checked = 0
    candidate = snap_to_step(
        max(local_start, align_preferred(local_start, config)),
        step,
        config,
    )
    while candidate < local_end and candidate + duration <= local_end:
        if candidate < local_start:
            candidate = max(local_start, align_preferred(local_start, config))
            continue
        checked += 1
        if slot_respects_rules(candidate, duration, config):
            return candidate, checked
        candidate = advance_candidate(candidate, step, duration, config)
    return None, checked

def find_slot_via_busy_gaps(
    *,
    earliest_allowed: datetime,
    search_end: datetime,
    duration: timedelta,
    step: timedelta,
    union_busy: list[tuple[datetime, datetime]],
    config: OutlookConfig,
) -> tuple[datetime | None, int]:
    """Ищет слот в промежутках между объединёнными блоками занятости."""
    checked = 0
    window_start = earliest_allowed

    for busy_start, busy_end in union_busy:
        if busy_start > search_end:
            break
        if busy_start > window_start:
            slot, window_checked = first_valid_slot_in_window(
                window_start,
                min(busy_start, search_end),
                duration=duration,
                step=step,
                config=config,
            )
            checked += window_checked
            if slot is not None:
                return slot, checked
        window_start = snap_to_step(max(window_start, busy_end), step, config)

    if window_start < search_end:
        slot, window_checked = first_valid_slot_in_window(
            window_start,
            search_end,
            duration=duration,
            step=step,
            config=config,
        )
        checked += window_checked
        if slot is not None:
            return slot, checked
    return None, checked

