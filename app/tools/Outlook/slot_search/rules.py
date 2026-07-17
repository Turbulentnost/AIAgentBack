from __future__ import annotations

from datetime import datetime, timedelta
from datetime import time as dt_time
from zoneinfo import ZoneInfo

from app.tools.Outlook.cancel_meeting import to_local
from app.tools.Outlook.outlook_config import OutlookConfig

from .constants import FORBIDDEN_BLOCKS, WORK_END, WORK_START


def combine(day: datetime, clock: dt_time, config: OutlookConfig) -> datetime:
    day = to_local(day, config)
    return day.replace(hour=clock.hour, minute=clock.minute, second=0, microsecond=0)

def is_workday(dt: datetime, config: OutlookConfig) -> bool:
    return to_local(dt, config).weekday() < 5

def next_workday_start(dt: datetime, config: OutlookConfig) -> datetime:
    dt = to_local(dt, config)
    candidate = combine(dt, WORK_START, config)
    if candidate <= dt:
        candidate += timedelta(days=1)
    while not is_workday(candidate, config):
        candidate += timedelta(days=1)
    return candidate

def align_preferred(preferred: datetime, config: OutlookConfig) -> datetime:
    """Первая точка перебора с учётом рабочего дня (не раньше WORK_START)."""
    current = to_local(preferred, config).replace(second=0, microsecond=0)
    if not is_workday(current, config):
        while not is_workday(current, config):
            current += timedelta(days=1)
        return combine(current, WORK_START, config)
    if current.time() < WORK_START:
        return combine(current, WORK_START, config)
    latest_start = combine(current, WORK_END, config) - timedelta(minutes=1)
    if current > latest_start:
        return next_workday_start(current, config)
    return current

def not_before_now(config: OutlookConfig) -> datetime:
    """Нижняя граница поиска — текущий момент (не дата из СЗ в прошлом)."""
    now = datetime.now(ZoneInfo(config.timezone)).replace(second=0, microsecond=0)
    return align_preferred(now, config)


def snap_to_step(dt: datetime, step: timedelta, config: OutlookConfig) -> datetime:
    """Округляет время вверх до сетки step (например 15 мин)."""
    dt = to_local(dt, config).replace(second=0, microsecond=0)
    step_minutes = max(int(step.total_seconds() // 60), 1)
    remainder = dt.minute % step_minutes
    if remainder == 0:
        return dt
    return dt + timedelta(minutes=step_minutes - remainder)

def intervals_overlap(a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime) -> bool:
    return a_start < b_end and a_end > b_start

def slot_respects_rules(start: datetime, duration: timedelta, config: OutlookConfig) -> bool:
    start = to_local(start, config)
    end = start + duration
    if not is_workday(start, config):
        return False
    if start.date() != end.date():
        return False
    if start.time() < WORK_START or end.time() > WORK_END:
        return False
    for block_start, block_end in (
        (combine(start, block[0], config), combine(start, block[1], config))
        for block in FORBIDDEN_BLOCKS
    ):
        if intervals_overlap(start, end, block_start, block_end):
            return False
    return True

def advance_candidate(
    current: datetime,
    step: timedelta,
    duration: timedelta,
    config: OutlookConfig,
) -> datetime:
    current = to_local(current, config) + step

    while True:
        if not is_workday(current, config):
            current = next_workday_start(current - timedelta(days=1), config)
            continue

        latest_start = combine(current, WORK_END, config) - duration
        if current.time() < WORK_START:
            current = combine(current, WORK_START, config)
            continue
        if current > latest_start:
            current = next_workday_start(current, config)
            continue
        return current

