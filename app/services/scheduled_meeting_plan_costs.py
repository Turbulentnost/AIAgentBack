from __future__ import annotations

import math
from datetime import datetime
from typing import Any, Literal

from app.models.enums import ScheduledMeetingWeekday
from app.schemas.scheduled_meeting import (
    ScheduledMeetingPlanConflictRead,
    ScheduledMeetingPlanDifficulty,
    ScheduledMeetingPlanOptionKind,
    ScheduledMeetingPlanOptionRead,
)
from app.tools.Outlook.slot_search.constants import MOVABILITY_RESCHEDULE_PENALTY

Difficulty = ScheduledMeetingPlanDifficulty
OptionKind = ScheduledMeetingPlanOptionKind

_WEEKDAY_TO_INT: dict[ScheduledMeetingWeekday, int] = {
    ScheduledMeetingWeekday.MONDAY: 0,
    ScheduledMeetingWeekday.TUESDAY: 1,
    ScheduledMeetingWeekday.WEDNESDAY: 2,
    ScheduledMeetingWeekday.THURSDAY: 3,
    ScheduledMeetingWeekday.FRIDAY: 4,
    ScheduledMeetingWeekday.SATURDAY: 5,
    ScheduledMeetingWeekday.SUNDAY: 6,
}


def difficulty_from_cost(cost: float | None) -> ScheduledMeetingPlanDifficulty | None:
    if cost is None:
        return None
    if cost < 1.5:
        return "easy"
    if cost < 3.5:
        return "medium"
    return "hard"


def cost_shift_ours(
    *,
    planned_start: datetime,
    suggested_start: datetime,
    anchor_weekday: ScheduledMeetingWeekday | None,
) -> float:
    """Стоимость сдвига нашей occurrence. Меньше — легче."""
    cost = 1.0
    if suggested_start.date() != planned_start.date():
        cost += 0.5
    # Только сдвиг времени суток — смена дня уже учтена отдельно.
    planned_minutes = planned_start.hour * 60 + planned_start.minute
    suggested_minutes = suggested_start.hour * 60 + suggested_start.minute
    delta_hours = abs(suggested_minutes - planned_minutes) / 60.0
    rounded_half_hours = math.ceil(delta_hours * 2) / 2.0
    cost += 0.25 * rounded_half_hours
    if anchor_weekday is not None:
        expected = _WEEKDAY_TO_INT.get(anchor_weekday)
        if expected is not None and suggested_start.weekday() != expected:
            cost += 1.0
    return round(cost, 4)


def cost_reschedule_blockers(
    blockers: list[ScheduledMeetingPlanConflictRead],
) -> float | None:
    """Стоимость переноса чужих blocker-встреч. None — вариант недоступен."""
    unique: dict[tuple[str, str, str], ScheduledMeetingPlanConflictRead] = {}
    for item in blockers:
        subject = (item.event_subject or "").strip()
        hint_start = (item.reschedule_hint_start or "").strip()
        if not subject or not hint_start:
            continue
        key = (
            str(item.event_start or ""),
            str(item.event_end or ""),
            subject.lower(),
        )
        unique[key] = item
    if not unique:
        return None
    total = 0.0
    for item in unique.values():
        movability = item.movability or "medium"
        total += MOVABILITY_RESCHEDULE_PENALTY.get(movability, 1.0)
    return round(total, 4)


def _option(
    *,
    kind: OptionKind,
    available: bool,
    cost: float | None = None,
    recommended: bool = False,
    suggested_start: str | None = None,
    suggested_end: str | None = None,
    blockers: list[ScheduledMeetingPlanConflictRead] | None = None,
    reason: str | None = None,
) -> ScheduledMeetingPlanOptionRead:
    return ScheduledMeetingPlanOptionRead(
        kind=kind,
        available=available,
        cost=cost,
        difficulty=difficulty_from_cost(cost) if available else None,
        recommended=recommended,
        suggested_start=suggested_start,
        suggested_end=suggested_end,
        blockers=list(blockers or []),
        reason=reason,
    )


def build_conflict_options(
    *,
    policy: Literal["strict", "soft_week", "skip"],
    planned_start: datetime,
    suggested_slot: tuple[datetime, datetime] | None,
    conflicts: list[ScheduledMeetingPlanConflictRead],
    anchor_weekday: ScheduledMeetingWeekday | None,
    format_slot,
) -> tuple[list[ScheduledMeetingPlanOptionRead], OptionKind | None]:
    """Собирает options и recommended kind для конфликтной даты."""
    if policy == "strict":
        option = _option(
            kind="keep_conflict",
            available=True,
            cost=0.0,
            recommended=True,
            reason="Оставить исходный слот даже при конфликтах",
        )
        return [option], "keep_conflict"

    if policy == "skip":
        option = _option(
            kind="skip",
            available=True,
            cost=0.0,
            recommended=True,
            reason="Пропустить эту дату серии",
        )
        return [option], "skip"

    shift_cost: float | None = None
    shift_start_label: str | None = None
    shift_end_label: str | None = None
    if suggested_slot is not None:
        suggested_start, suggested_end = suggested_slot
        shift_cost = cost_shift_ours(
            planned_start=planned_start,
            suggested_start=suggested_start,
            anchor_weekday=anchor_weekday,
        )
        shift_start_label = format_slot(suggested_start)
        shift_end_label = format_slot(suggested_end)

    blockers = [
        item
        for item in conflicts
        if (item.event_subject or "").strip() and (item.reschedule_hint_start or "").strip()
    ]
    blockers_cost = cost_reschedule_blockers(blockers)

    candidates: list[tuple[OptionKind, float]] = []
    if shift_cost is not None:
        candidates.append(("shift_ours", shift_cost))
    if blockers_cost is not None:
        candidates.append(("reschedule_blockers", blockers_cost))

    recommended: OptionKind | None
    if candidates:
        # При равенстве предпочитаем A (shift_ours) — сортировка (cost, kind_rank).
        kind_rank = {"shift_ours": 0, "reschedule_blockers": 1}
        recommended = min(candidates, key=lambda item: (item[1], kind_rank[item[0]]))[0]
    else:
        recommended = "keep_conflict"

    options = [
        _option(
            kind="shift_ours",
            available=shift_cost is not None,
            cost=shift_cost,
            recommended=recommended == "shift_ours",
            suggested_start=shift_start_label,
            suggested_end=shift_end_label,
            reason=(
                "Сдвинуть нашу встречу на свободное окно той же недели"
                if shift_cost is not None
                else "На этой неделе нет общего свободного окна"
            ),
        ),
        _option(
            kind="reschedule_blockers",
            available=blockers_cost is not None,
            cost=blockers_cost,
            recommended=recommended == "reschedule_blockers",
            blockers=blockers,
            reason=(
                "Оставить наш слот и предложить перенести конфликтующие встречи"
                if blockers_cost is not None
                else "Нет идентифицируемых встреч с доступным окном переноса"
            ),
        ),
        _option(
            kind="keep_conflict",
            available=True,
            cost=None,
            recommended=recommended == "keep_conflict",
            reason="Оставить исходный слот с конфликтами",
        ),
        _option(
            kind="skip",
            available=True,
            cost=None,
            recommended=False,
            reason="Пропустить эту дату серии",
        ),
    ]
    return options, recommended


def unique_blocker_key(item: dict[str, Any] | ScheduledMeetingPlanConflictRead) -> tuple[str, str, str]:
    if isinstance(item, ScheduledMeetingPlanConflictRead):
        return (
            str(item.event_start or ""),
            str(item.event_end or ""),
            (item.event_subject or "").strip().lower(),
        )
    return (
        str(item.get("event_start") or ""),
        str(item.get("event_end") or ""),
        str(item.get("event_subject") or "").strip().lower(),
    )
