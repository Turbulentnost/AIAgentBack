"""Incoming-control SLA norms (§2.3 / СТО-10-095)."""

from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

MOSCOW = ZoneInfo("Europe/Moscow")

# Working-hour approximations for MVP (calendar hours ≈ working hours for drafts).
SLA_ASSIGN_ENGINEER_WH = 2.0
SLA_DOC_CHECK_H = 0.5
SLA_IDENTIFY_RESULT_H = 1.0
SLA_NC_ACT_H = 0.5
SLA_OTK_CONFIRM_WH = 1.0
SLA_ZDK_REVIEW_WH = 8.0
SLA_PRESENT_TO_OTK_H = 1.0
SLA_HOUSEHOLD_CONTROL_WH = 8.0
ZDK_HANDOFF_DEADLINE = time(16, 0)


def hours_elapsed(started_at: datetime | None, now: datetime | None = None) -> float | None:
    if started_at is None:
        return None
    current = now or datetime.now(MOSCOW)
    start = started_at if started_at.tzinfo else started_at.replace(tzinfo=MOSCOW)
    if current.tzinfo is None:
        current = current.replace(tzinfo=MOSCOW)
    return max(0.0, (current - start).total_seconds() / 3600.0)


def within_sla(elapsed_h: float | None, limit_h: float) -> bool | None:
    if elapsed_h is None:
        return None
    return elapsed_h <= limit_h


def zdk_handoff_due_today(now: datetime | None = None) -> datetime:
    current = now or datetime.now(MOSCOW)
    if current.tzinfo is None:
        current = current.replace(tzinfo=MOSCOW)
    local = current.astimezone(MOSCOW)
    due = datetime.combine(local.date(), ZDK_HANDOFF_DEADLINE, tzinfo=MOSCOW)
    if local > due:
        due = due + timedelta(days=1)
    return due


def build_deadlines(
    *,
    category: str | None = None,
    assigned_at: datetime | None = None,
    presented_at: datetime | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    from app.agents.quality_control_agent.rules_registry import control_deadline_wd

    current = now or datetime.now(MOSCOW)
    deadlines: dict[str, Any] = {
        "assign_engineer_wh": SLA_ASSIGN_ENGINEER_WH,
        "doc_check_h": SLA_DOC_CHECK_H,
        "identify_result_h": SLA_IDENTIFY_RESULT_H,
        "nc_act_h": SLA_NC_ACT_H,
        "otk_confirm_wh": SLA_OTK_CONFIRM_WH,
        "zdk_review_wh": SLA_ZDK_REVIEW_WH,
        "present_to_otk_h": SLA_PRESENT_TO_OTK_H,
        "industrial_control_wd": control_deadline_wd(category),
        "zdk_handoff_by": zdk_handoff_due_today(current).isoformat(),
    }
    if presented_at is not None:
        deadlines["presented_at"] = presented_at.isoformat()
        deadlines["present_elapsed_h"] = hours_elapsed(presented_at, current)
        deadlines["present_within_sla"] = within_sla(
            deadlines["present_elapsed_h"], SLA_PRESENT_TO_OTK_H
        )
    if assigned_at is not None:
        deadlines["assigned_at"] = assigned_at.isoformat()
        deadlines["assign_elapsed_h"] = hours_elapsed(assigned_at, current)
        deadlines["assign_within_sla"] = within_sla(
            deadlines["assign_elapsed_h"], SLA_ASSIGN_ENGINEER_WH
        )
    return deadlines


__all__ = [
    "MOSCOW",
    "SLA_ASSIGN_ENGINEER_WH",
    "SLA_DOC_CHECK_H",
    "SLA_HOUSEHOLD_CONTROL_WH",
    "SLA_IDENTIFY_RESULT_H",
    "SLA_NC_ACT_H",
    "SLA_OTK_CONFIRM_WH",
    "SLA_PRESENT_TO_OTK_H",
    "SLA_ZDK_REVIEW_WH",
    "ZDK_HANDOFF_DEADLINE",
    "build_deadlines",
    "hours_elapsed",
    "within_sla",
    "zdk_handoff_due_today",
]
