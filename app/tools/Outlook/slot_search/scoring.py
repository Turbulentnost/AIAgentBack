from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from app.tools.Outlook.cancel_meeting import to_local
from app.tools.Outlook.outlook_config import OutlookConfig

from .availability import partition_attendees_at_slot
from .busy import fetch_all_busy_intervals, fetch_freebusy_calendar_events
from .conflicts import build_conflict_records
from .constants import (
    IMPACT_BUSY_ATTENDEE_WEIGHT,
    IMPACT_CONFLICT_WEIGHT,
    IMPACT_COVERAGE_WEIGHT,
    IMPACT_LEADERSHIP_BUSY,
    IMPACT_REQUIRED_FAIL,
    MOVABILITY_RESCHEDULE_PENALTY,
    QUORUM_RANK_SHORTLIST_MULTIPLIER,
)
from .timing import timed_step


def quorum_confidence(*, coverage_ratio: float, required_ok: bool, verified: bool, conflicts: int) -> float:
    if not required_ok:
        return 0.5
    confidence = 0.55 + coverage_ratio * 0.35
    if verified:
        confidence += 0.05
    if conflicts == 0:
        confidence += 0.05
    return min(confidence, 0.99)

def coverage_ratios(
    free_attendees: list[str],
    attendees: list[str],
    attendee_weights: dict[str, float] | None,
) -> tuple[float, float]:
    """Возвращает (weighted_ratio, flat_ratio)."""
    attendee_set = set(attendees)
    flat_ratio = len(free_attendees) / len(attendees) if attendees else 0.0
    if not attendee_weights:
        return flat_ratio, flat_ratio
    total_weight = sum(attendee_weights.get(email, 1.0) for email in attendees)
    if total_weight <= 0:
        return flat_ratio, flat_ratio
    free_weight = sum(
        attendee_weights.get(email, 1.0)
        for email in free_attendees
        if email in attendee_set
    )
    return free_weight / total_weight, flat_ratio

def busy_attendee_weight_cost(
    busy_attendees: list[str],
    attendee_weights: dict[str, float] | None,
) -> float:
    if not busy_attendees:
        return 0.0
    if not attendee_weights:
        return float(len(busy_attendees))
    return sum(attendee_weights.get(email, 1.0) for email in busy_attendees)

def conflict_reschedule_cost(
    conflicts: list[dict[str, Any]],
    attendee_weights: dict[str, float] | None,
) -> float:
    total = 0.0
    for conflict in conflicts:
        email = str(conflict.get("email") or "")
        weight = attendee_weights.get(email, 1.0) if attendee_weights else 1.0
        movability = str(conflict.get("movability") or "medium")
        penalty = MOVABILITY_RESCHEDULE_PENALTY.get(movability, 1.0)
        total += weight * penalty
    return total

def preliminary_slot_impact(
    *,
    score_ratio: float,
    busy_attendees: list[str],
    required: list[str],
    required_ok: bool,
    attendee_weights: dict[str, float] | None,
) -> float:
    """Меньше — лучше. Быстрая оценка до построения conflicts."""
    impact = (1.0 - score_ratio) * IMPACT_COVERAGE_WEIGHT
    impact += busy_attendee_weight_cost(busy_attendees, attendee_weights) * IMPACT_BUSY_ATTENDEE_WEIGHT
    required_set = set(required)
    impact += sum(1 for email in busy_attendees if email in required_set) * IMPACT_LEADERSHIP_BUSY
    if not required_ok:
        impact += IMPACT_REQUIRED_FAIL
    return round(impact, 4)

def slot_impact_score(
    *,
    weighted_coverage_ratio: float,
    required_ok: bool,
    busy_attendees: list[str],
    required: list[str],
    conflicts: list[dict[str, Any]],
    attendee_weights: dict[str, float] | None,
) -> float:
    """Меньше — лучше. Учитывает покрытие, должности занятых и переносимость конфликтов."""
    impact = (1.0 - weighted_coverage_ratio) * IMPACT_COVERAGE_WEIGHT
    impact += conflict_reschedule_cost(conflicts, attendee_weights) * IMPACT_CONFLICT_WEIGHT
    required_set = set(required)
    impact += sum(1 for email in busy_attendees if email in required_set) * IMPACT_LEADERSHIP_BUSY
    if not required_ok:
        impact += IMPACT_REQUIRED_FAIL
    return round(impact, 4)

def count_low_movability_conflicts(conflicts: list[dict[str, Any]]) -> int:
    return sum(1 for conflict in conflicts if str(conflict.get("movability") or "") == "low")

def count_easy_reschedule_conflicts(conflicts: list[dict[str, Any]]) -> int:
    return sum(1 for conflict in conflicts if str(conflict.get("movability") or "") == "high")

def _slot_preference_distance(
    slot_start: datetime,
    preferred: datetime,
    config: OutlookConfig,
) -> float:
    return abs((to_local(slot_start, config) - to_local(preferred, config)).total_seconds())

def _quorum_pool_sort_key(
    item: dict[str, Any],
    *,
    preferred: datetime,
    config: OutlookConfig,
) -> tuple[float, float, float, datetime]:
    slot_start: datetime = item["slot_start"]
    return (
        item["preliminary_impact"],
        -item["score_ratio"],
        _slot_preference_distance(slot_start, preferred, config),
        slot_start,
    )

def _quorum_candidate_sort_key(
    item: dict[str, Any],
    *,
    preferred: datetime,
    config: OutlookConfig,
) -> tuple[int, int, float, float, str]:
    slot_start = datetime.fromisoformat(item["slot_start"])
    reschedule_count = int(item.get("reschedule_count") or 0)
    easy_count = int(item.get("easy_reschedule_count") or 0)
    hard_reschedules = max(reschedule_count - easy_count, 0)
    return (
        int(item.get("low_movability_count") or 0),
        hard_reschedules,
        _slot_preference_distance(slot_start, preferred, config),
        float(item.get("impact_score") or 999.0),
        item["slot_start"],
    )

def _build_quorum_candidate_payload(
    *,
    item: dict[str, Any],
    attendees: list[str],
    required: list[str],
    required_set: set[str],
    busy_by_attendee: dict[str, list[tuple[datetime, datetime]]],
    attendee_weights: dict[str, float] | None,
    config: OutlookConfig,
    duration: timedelta,
    step: timedelta,
    search_end: datetime,
    verified: bool,
    free_attendees: list[str],
    busy_attendees: list[str],
) -> dict[str, Any]:
    slot_start: datetime = item["slot_start"]
    slot_end: datetime = item["slot_end"]
    conflict_window_end = min(search_end, slot_end + timedelta(days=3))
    conflict_events = fetch_freebusy_calendar_events(
        config,
        busy_attendees,
        slot_start - timedelta(hours=1),
        slot_end + timedelta(hours=1),
    ) if busy_attendees else {}

    conflicts: list[dict[str, Any]] = []
    for email in busy_attendees:
        conflicts.extend(
            build_conflict_records(
                email=email,
                slot_start=slot_start,
                duration=duration,
                busy_intervals=busy_by_attendee.get(email, []),
                calendar_events=conflict_events.get(email, []),
                config=config,
                step=step,
                search_end=conflict_window_end,
            )
        )

    weighted_ratio, _flat_ratio = coverage_ratios(
        free_attendees,
        attendees,
        attendee_weights,
    )
    required_ok = all(email in free_attendees for email in required_set)
    impact_score = slot_impact_score(
        weighted_coverage_ratio=weighted_ratio,
        required_ok=required_ok,
        busy_attendees=busy_attendees,
        required=required,
        conflicts=conflicts,
        attendee_weights=attendee_weights,
    )
    return {
        "slot_start": slot_start.isoformat(),
        "slot_end": slot_end.isoformat(),
        "duration_minutes": int(duration.total_seconds() // 60),
        "coverage": {
            "free": len(free_attendees),
            "total": len(attendees),
            "ratio": round(len(free_attendees) / len(attendees), 4),
            "weighted_ratio": round(weighted_ratio, 4),
            "required_ok": required_ok,
        },
        "free_attendees": free_attendees,
        "busy_attendees": busy_attendees,
        "conflicts": conflicts,
        "verified": verified,
        "impact_score": impact_score,
        "busy_weight_cost": round(
            busy_attendee_weight_cost(busy_attendees, attendee_weights),
            4,
        ),
        "reschedule_count": len(conflicts),
        "easy_reschedule_count": count_easy_reschedule_conflicts(conflicts),
        "low_movability_count": count_low_movability_conflicts(conflicts),
        "confidence": quorum_confidence(
            coverage_ratio=weighted_ratio if attendee_weights else len(free_attendees) / len(attendees),
            required_ok=required_ok,
            verified=verified,
            conflicts=len(conflicts),
        ),
    }

