from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from app.agents.tasks_agent.table_presenter import compute_overdue_days, format_task_status
from app.tools.onec.get_porucheniya import parse_onec_datetime

METRICS_DEFINITIONS: list[tuple[str, str]] = [
    ("total_under_control", "Всего задач на контроле"),
    ("new_tasks_today", "Новые задачи за день"),
    ("due_today", "Задачи со сроком сегодня"),
    ("due_in_1_3_business_days", "Задачи со сроком в ближайшие 1–3 рабочих дня"),
    ("overdue_tasks", "Просроченные задачи"),
    ("critical_overdues", "Критические просрочки"),
    ("completed_without_artifact", "Выполненные задачи без артефакта"),
    ("completed_without_confirmation", "Выполненные задачи без подтверждения"),
    ("postponement_requested", "Запрошен перенос срока"),
    ("postponement_approved", "Перенос согласован"),
    ("postponement_without_basis", "Перенос без основания"),
    ("rk_questions", "Вопросы для вынесения на РК"),
    ("closed_today", "Задачи закрыты за день"),
]

_COMPLETED_STATUSES = frozenset({"Выполнено", "Закрыто"})


def _to_date(value: str | date | datetime | None) -> date | None:
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    parsed = parse_onec_datetime(str(value))
    return parsed.date() if parsed else None


def _is_truthy_marker(value: Any) -> bool:
    text = str(value or "").strip().lower()
    return text in {"да", "yes", "true", "1", "требуется"}


def _has_artifact(value: Any) -> bool:
    text = str(value or "").strip().lower()
    if not text:
        return False
    return text not in {"нет", "no", "false", "0"}


def _is_business_day(day: date) -> bool:
    return day.weekday() < 5


def _add_business_days(start: date, business_days: int) -> date:
    current = start
    added = 0
    while added < business_days:
        current += timedelta(days=1)
        if _is_business_day(current):
            added += 1
    return current


def _due_in_business_day_window(due: date, report_day: date, *, min_days: int, max_days: int) -> bool:
    if due <= report_day:
        return False
    earliest = _add_business_days(report_day, min_days)
    latest = _add_business_days(report_day, max_days)
    return earliest <= due <= latest


def _postponement_fields(task: dict[str, Any]) -> tuple[str, str, bool]:
    request = str(task.get("postponement_request") or "").strip()
    basis = str(task.get("postponement_basis") or "").strip()
    approved = _is_truthy_marker(task.get("postponement_approved"))
    if request or basis or approved:
        return request, basis, approved

    note = str(task.get("note") or task.get("comment") or "").strip()
    if not note:
        return "", "", False
    lower = note.lower()
    if "перенос" not in lower:
        return "", "", False
    approved = any(marker in lower for marker in ("согласован", "утвержден", "одобрен"))
    return note, basis, approved


def _iter_control_tasks(
    porucheniya: list[dict[str, Any]],
    protocols: list[dict[str, Any]],
    *,
    now: datetime,
) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    for document in porucheniya:
        document_status = document.get("status")
        document_completed = document_status in _COMPLETED_STATUSES
        for task in document.get("tasks") or []:
            completed = bool(task.get("completed")) or document_completed
            tasks.append(
                {
                    "document_date": _to_date(document.get("document_date")),
                    "assigned_date": _to_date(task.get("assigned_date")),
                    "due_date": _to_date(task.get("due_date")),
                    "completed_date": _to_date(task.get("completed_date")),
                    "completed": completed,
                    "confirmed": bool(task.get("confirmed")),
                    "has_file": _has_artifact(task.get("has_file")),
                    "overdue_days": compute_overdue_days(task.get("due_date"), now=now),
                    "priority": str(task.get("priority") or ""),
                    "status": format_task_status(
                        document_status,
                        overdue_days=compute_overdue_days(task.get("due_date"), now=now),
                    ),
                    "rk_required": task.get("rk_required"),
                    "controller_action": str(task.get("controller_action") or ""),
                    **_unpack_postponement(task),
                }
            )

    for document in protocols:
        document_status = document.get("status")
        for task in document.get("tasks") or []:
            completed = bool(task.get("completed")) or document_status in _COMPLETED_STATUSES
            request, basis, approved = _postponement_fields(task)
            tasks.append(
                {
                    "document_date": _to_date(document.get("document_date")),
                    "assigned_date": _to_date(task.get("assigned_date")),
                    "due_date": _to_date(task.get("due_date")),
                    "completed_date": _to_date(task.get("completed_date")),
                    "completed": completed,
                    "confirmed": bool(task.get("confirmed")),
                    "has_file": _has_artifact(task.get("has_file")),
                    "overdue_days": compute_overdue_days(task.get("due_date"), now=now),
                    "priority": str(task.get("priority") or ""),
                    "status": format_task_status(
                        document_status,
                        overdue_days=compute_overdue_days(task.get("due_date"), now=now),
                    ),
                    "rk_required": task.get("rk_required"),
                    "controller_action": str(task.get("controller_action") or ""),
                    "postponement_request": request,
                    "postponement_basis": basis,
                    "postponement_approved": approved,
                }
            )
    return tasks


def _unpack_postponement(task: dict[str, Any]) -> dict[str, Any]:
    request, basis, approved = _postponement_fields(task)
    return {
        "postponement_request": request,
        "postponement_basis": basis,
        "postponement_approved": approved,
    }


def _is_new_task_for_day(task: dict[str, Any], report_day: date) -> bool:
    for field in ("document_date", "assigned_date"):
        value = task.get(field)
        if value == report_day:
            return True
    return False


def build_tasks_metrics(
    porucheniya: list[dict[str, Any]],
    protocols: list[dict[str, Any]],
    *,
    summary_note: str,
    report_day: date | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = (now or datetime.now()).replace(microsecond=0)
    day = report_day or current.date()
    tasks = _iter_control_tasks(porucheniya, protocols, now=current)

    counts_map = {
        "total_under_control": len(tasks),
        "new_tasks_today": sum(1 for task in tasks if _is_new_task_for_day(task, day)),
        "due_today": sum(1 for task in tasks if task.get("due_date") == day and not task.get("completed")),
        "due_in_1_3_business_days": sum(
            1
            for task in tasks
            if task.get("due_date") is not None
            and not task.get("completed")
            and _due_in_business_day_window(task["due_date"], day, min_days=1, max_days=3)
        ),
        "overdue_tasks": sum(
            1
            for task in tasks
            if (task.get("overdue_days") or 0) > 0 and task.get("priority") != "Критический"
        ),
        "critical_overdues": sum(1 for task in tasks if task.get("priority") == "Критический"),
        "completed_without_artifact": sum(
            1 for task in tasks if task.get("completed") and not task.get("has_file")
        ),
        "completed_without_confirmation": sum(
            1 for task in tasks if task.get("completed") and not task.get("confirmed")
        ),
        "postponement_requested": sum(1 for task in tasks if task.get("postponement_request")),
        "postponement_approved": sum(
            1
            for task in tasks
            if task.get("postponement_approved")
            or "согласован" in str(task.get("controller_action") or "").lower()
        ),
        "postponement_without_basis": sum(
            1
            for task in tasks
            if task.get("postponement_request") and not task.get("postponement_basis")
        ),
        "rk_questions": sum(
            1
            for task in tasks
            if _is_truthy_marker(task.get("rk_required"))
            or "рк" in str(task.get("controller_action") or "").lower()
        ),
        "closed_today": sum(
            1 for task in tasks if task.get("completed") and task.get("completed_date") == day
        ),
    }

    rows = []
    for key, title in METRICS_DEFINITIONS:
        note = summary_note if key == "total_under_control" else None
        rows.append(
            {
                "key": key,
                "title": title,
                "count": counts_map.get(key, 0),
                "note": note,
            }
        )

    return {
        "rows": rows,
        "counts": counts_map,
        "report_day": day.isoformat(),
    }
