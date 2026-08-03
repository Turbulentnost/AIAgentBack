from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.services.meeting_memo_document import (
    clean_text as _clean_text,
    is_empty_odata_date,
    looks_like_guid as _looks_like_guid,
    parse_odata_datetime,
    parse_odata_time_component,
    resolve_meeting_manager_key,
    resolve_meeting_schedule,
    schedule_duration_minutes as duration_minutes,
    is_meeting_manager_specified,
)
from app.tools.onec.get_meetings import meeting_theme, theme_matches
from app.tools.onec.lookup_user_ref import is_empty_key

STO_DIRECTION_LABEL = "Управление делами"
AUTO_APPROVE_SERVICE_MEMO = False

STO_CHECKLIST_DEFINITIONS: tuple[tuple[str, str], ...] = (
    ("direction", f"Направление: {STO_DIRECTION_LABEL}"),
    ("meeting_theme", "Тема совещания"),
    ("participants", "Список участников"),
    ("meeting_manager", "Руководитель совещания"),
    ("meeting_goal", "Цель совещания"),
    ("meeting_tasks", "Задачи в плане совещания"),
    ("priority", "Приоритет"),
    ("desired_meeting_date", "Желаемая дата проведения"),
    ("meeting_time", "Время начала совещания"),
    ("duration", "Длительность совещания"),
    ("location", "Место проведения"),
)


@dataclass(slots=True)
class MemoValidationIssue:
    field: str
    severity: str
    message: str


def normalize_direction(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "").strip().lower())


def is_office_management_direction(value: str | None) -> bool:
    normalized = normalize_direction(value)
    if not normalized:
        return False
    compact = normalized.replace(" ", "")
    return compact == "управлениеделами"


def is_sto_direction_valid(header: dict[str, Any]) -> bool:
    """СТО: «Управление делами» или СЗ с темой «Организация совещаний» (в 1С часто enum ПрочиеВнутренние)."""
    direction = _clean_text(header.get("Направление"))
    if is_office_management_direction(direction):
        return True
    return theme_matches(header, None)


def _document_header(document: dict[str, Any] | None) -> dict[str, Any]:
    if not document:
        return {}
    return document.get("header") or document.get("memo") or document


def _participants_in_tabular(document: dict[str, Any]) -> bool:
    header = _document_header(document)
    inline = header.get("СписокУчастников")
    if isinstance(inline, list):
        for row in inline:
            if isinstance(row, dict) and _is_participant_row(row):
                return True

    sections = document.get("tabular_sections") or {}
    for section_name, rows in sections.items():
        if "Участник" not in section_name or not isinstance(rows, list):
            continue
        for row in rows:
            if isinstance(row, dict) and _is_participant_row(row):
                return True
    return False


def _is_participant_row(row: dict[str, Any]) -> bool:
    participant_key = row.get("Участник_Key")
    if isinstance(participant_key, str) and not is_empty_key(participant_key):
        return True
    participant = row.get("Участник")
    if isinstance(participant, dict):
        return True
    for key, value in row.items():
        if "ФИО" in key and isinstance(value, str) and value.strip():
            return True
    return False


def _count_participants(document: dict[str, Any]) -> int:
    header = _document_header(document)
    inline = header.get("СписокУчастников")
    if isinstance(inline, list):
        count = sum(1 for row in inline if isinstance(row, dict) and _is_participant_row(row))
        if count:
            return count

    participants = document.get("participants") or []
    if participants:
        return len(participants)

    sections = document.get("tabular_sections") or {}
    for section_name, rows in sections.items():
        if "Участник" not in section_name or not isinstance(rows, list):
            continue
        count = sum(1 for row in rows if isinstance(row, dict) and _is_participant_row(row))
        if count:
            return count
    return 0


def _meeting_plan_rows(header: dict[str, Any], document: dict[str, Any]) -> list[dict[str, Any]]:
    inline = header.get("ПланСовещания")
    if isinstance(inline, list):
        return [row for row in inline if isinstance(row, dict)]

    sections = document.get("tabular_sections") or {}
    rows = sections.get("ПланСовещания")
    if isinstance(rows, list):
        return [row for row in rows if isinstance(row, dict)]
    return []


def _meeting_tasks(plan_rows: list[dict[str, Any]]) -> list[str]:
    tasks: list[str] = []
    for row in plan_rows:
        task = _clean_text(row.get("Задача"))
        if task:
            tasks.append(task)
    return tasks


def _location_specified(header: dict[str, Any]) -> bool:
    raw = header.get("МестоПроведенияСовещания")
    if isinstance(raw, dict):
        return bool(_clean_text(raw.get("Description")))
    text = _clean_text(raw)
    if not text:
        return False
    if _looks_like_guid(text):
        return not is_empty_key(text)
    return True


def _priority_specified(header: dict[str, Any]) -> bool:
    priority_key = header.get("Приоритет_Key")
    if isinstance(priority_key, str) and not is_empty_key(priority_key):
        return True
    raw = header.get("Приоритет")
    if isinstance(raw, dict):
        return bool(_clean_text(raw.get("Description")))
    text = _clean_text(raw)
    return bool(text) and not _looks_like_guid(text)


def _meeting_theme_specified(header: dict[str, Any]) -> bool:
    return bool(_clean_text(header.get("ТемаСовещания")))


def validate_meeting_memo_sto(document: dict[str, Any] | None) -> list[MemoValidationIssue]:
    """Проверяет обязательные условия СТО для служебной записки на совещание."""
    if not document:
        return [
            MemoValidationIssue(
                field="memo",
                severity="error",
                message="Служебная записка не загружена",
            )
        ]

    header = _document_header(document)
    issues: list[MemoValidationIssue] = []

    direction = _clean_text(header.get("Направление"))
    if not is_sto_direction_valid(header):
        issues.append(
            MemoValidationIssue(
                field="direction",
                severity="error",
                message=(
                    f"Направление должно быть «{STO_DIRECTION_LABEL}»"
                    f"{f', указано «{direction}»' if direction else ''}"
                ),
            )
        )

    if not _meeting_theme_specified(header):
        issues.append(
            MemoValidationIssue(
                field="meeting_theme",
                severity="error",
                message="Не указана тема совещания",
            )
        )

    participants_count = _count_participants(document)
    if participants_count == 0:
        issues.append(
            MemoValidationIssue(
                field="participants",
                severity="error",
                message="Не указан список участников",
            )
        )

    if not is_meeting_manager_specified(header, application=document.get("application")):
        issues.append(
            MemoValidationIssue(
                field="meeting_manager",
                severity="error",
                message="Не указан руководитель совещания",
            )
        )

    goal = _clean_text(header.get("ЦельПланаСовещания"))
    plan_rows = _meeting_plan_rows(header, document)
    tasks = _meeting_tasks(plan_rows)
    if not goal:
        issues.append(
            MemoValidationIssue(
                field="meeting_goal",
                severity="error",
                message="Не указана цель совещания",
            )
        )
    if not tasks:
        issues.append(
            MemoValidationIssue(
                field="meeting_tasks",
                severity="error",
                message="Не указана ни одна задача в плане совещания",
            )
        )

    if not _priority_specified(header):
        issues.append(
            MemoValidationIssue(
                field="priority",
                severity="error",
                message="Не указан приоритет",
            )
        )

    desired_date = parse_odata_datetime(header.get("ЖелаемаяДатаПроведенияСовещания"))
    actual_date = parse_odata_datetime(header.get("ДатаПроведенияСовещания"))
    if desired_date is None and actual_date is None:
        issues.append(
            MemoValidationIssue(
                field="desired_meeting_date",
                severity="error",
                message="Не указана желаемая дата проведения совещания",
            )
        )

    start, end = resolve_meeting_schedule(header)
    if start is None:
        issues.append(
            MemoValidationIssue(
                field="meeting_time",
                severity="error",
                message="Не указано время начала совещания",
            )
        )

    if duration_minutes(start, end) is None:
        issues.append(
            MemoValidationIssue(
                field="duration",
                severity="error",
                message="Не указана длительность совещания",
            )
        )

    if not _location_specified(header):
        issues.append(
            MemoValidationIssue(
                field="location",
                severity="error",
                message="Не указано место проведения совещания",
            )
        )

    return issues


def sto_issues_to_dicts(issues: list[MemoValidationIssue]) -> list[dict[str, str]]:
    return [{"field": issue.field, "message": issue.message} for issue in issues]


def is_sto_ready(issues: list[MemoValidationIssue]) -> bool:
    return not issues


def sto_ready_message() -> str:
    return "Все условия СТО выполнены. Заявка готова к согласованию сотрудником УД."


def sto_auto_approve_message() -> str:
    return sto_ready_message()


def sto_ud_recommendation(issues: list[MemoValidationIssue]) -> str:
    messages = [issue.message for issue in issues]
    if not messages:
        return sto_auto_approve_message()
    if len(messages) == 1:
        return f"Сотруднику УД: перед согласованием проверьте — {messages[0]}."
    joined = "; ".join(messages)
    return f"Сотруднику УД: перед согласованием проверьте заявку — {joined}."


def _location_description(header: dict[str, Any]) -> str | None:
    raw = header.get("МестоПроведенияСовещания")
    if isinstance(raw, dict):
        return _clean_text(raw.get("Description"))
    return _clean_text(raw)


def _format_checklist_date(value: str | None) -> str | None:
    if not value:
        return None
    parsed = parse_odata_datetime(value)
    if parsed is None:
        return _clean_text(value)
    return parsed.strftime("%d.%m.%Y")


def _sto_checklist_pass_message(field: str, document: dict[str, Any], label: str) -> str:
    header = _document_header(document)
    if field == "direction":
        direction = _clean_text(header.get("Направление")) or STO_DIRECTION_LABEL
        return f"{label}: {direction}"
    if field == "meeting_theme":
        return _clean_text(header.get("ТемаСовещания")) or label
    if field == "desired_meeting_date":
        raw = _clean_text(header.get("ЖелаемаяДатаПроведенияСовещания")) or _clean_text(
            header.get("ДатаПроведенияСовещания")
        )
        return _format_checklist_date(raw) or label
    if field == "meeting_time":
        start, _ = resolve_meeting_schedule(header)
        return start.strftime("%d.%m.%Y, %H:%M") if start else label
    if field == "duration":
        start, end = resolve_meeting_schedule(header)
        minutes = duration_minutes(start, end)
        return f"{minutes} мин." if minutes else label
    if field == "location":
        return _location_description(header) or label
    if field == "meeting_goal":
        return _clean_text(header.get("ЦельПланаСовещания")) or label
    if field == "priority":
        raw = header.get("Приоритет")
        if isinstance(raw, dict):
            return _clean_text(raw.get("Description")) or label
        return _clean_text(raw) or label
    if field == "participants":
        count = _count_participants(document)
        return f"Участников: {count}" if count else label
    if field == "meeting_manager":
        return "Руководитель указан"
    if field == "meeting_tasks":
        tasks = _meeting_tasks(_meeting_plan_rows(header, document))
        return f"Задач в плане: {len(tasks)}" if tasks else label
    return label


def build_sto_checklist(document: dict[str, Any] | None) -> list[dict[str, Any]]:
    issues = validate_meeting_memo_sto(document)
    issue_by_field = {issue.field: issue for issue in issues}
    checklist: list[dict[str, Any]] = []
    for field, label in STO_CHECKLIST_DEFINITIONS:
        issue = issue_by_field.get(field)
        passed = issue is None
        checklist.append(
            {
                "field": field,
                "label": label,
                "passed": passed,
                "message": issue.message if issue else _sto_checklist_pass_message(field, document or {}, label),
            }
        )
    return checklist


def build_sto_payload(document: dict[str, Any] | None) -> dict[str, Any]:
    assessment = assess_sto_readiness(document)
    assessment["sto_checklist"] = build_sto_checklist(document)
    return assessment


def assess_sto_readiness(document: dict[str, Any] | None) -> dict[str, Any]:
    issues = validate_meeting_memo_sto(document)
    ready = is_sto_ready(issues)
    return {
        "sto_ready": ready,
        "sto_issues": sto_issues_to_dicts(issues),
        "ud_recommendation": sto_ready_message() if ready else sto_ud_recommendation(issues),
        "auto_approve_allowed": ready and AUTO_APPROVE_SERVICE_MEMO,
    }


def sto_validation_summary(issues: list[MemoValidationIssue]) -> str:
    messages = [issue.message for issue in issues if issue.severity == "error"]
    if not messages:
        return ""
    if len(messages) == 1:
        return messages[0]
    return "Не выполнены условия СТО: " + "; ".join(messages)


def validate_meeting_memo_document(document: dict[str, Any] | None) -> list[MemoValidationIssue]:
    """Проверяет структуру служебной записки из 1С перед организацией совещания."""
    if not document:
        return [
            MemoValidationIssue(
                field="memo",
                severity="error",
                message="Служебная записка не загружена",
            )
        ]

    memo = document.get("memo") or document.get("header") or {}
    issues: list[MemoValidationIssue] = []

    if memo.get("DeletionMark"):
        issues.append(
            MemoValidationIssue(
                field="DeletionMark",
                severity="error",
                message="Документ помечен на удаление",
            )
        )

    if not memo.get("Ref_Key"):
        issues.append(
            MemoValidationIssue(
                field="Ref_Key",
                severity="error",
                message="Не указан идентификатор документа (Ref_Key)",
            )
        )

    if not memo.get("Number"):
        issues.append(
            MemoValidationIssue(
                field="Number",
                severity="error",
                message="Не указан номер служебной записки",
            )
        )

    if not memo.get("Date"):
        issues.append(
            MemoValidationIssue(
                field="Date",
                severity="warning",
                message="Не указана дата служебной записки",
            )
        )

    participants = document.get("participants") or []
    if not participants and not _participants_in_tabular(document):
        issues.append(
            MemoValidationIssue(
                field="participants",
                severity="error",
                message="Не указаны участники совещания",
            )
        )

    if not memo.get("ТемаСлужебнойЗаписки") and not memo.get("ТемаСлужебнойЗаписки_Key"):
        issues.append(
            MemoValidationIssue(
                field="ТемаСлужебнойЗаписки",
                severity="warning",
                message="Не указана тема служебной записки",
            )
        )

    sto_issues = validate_meeting_memo_sto(document)
    seen_fields = {issue.field for issue in issues}
    for issue in sto_issues:
        if issue.field in seen_fields:
            continue
        issues.append(issue)
        seen_fields.add(issue.field)

    return [item for item in issues if item.severity == "error"] or issues


def validate_memo_series_planning(document: dict[str, Any] | None) -> list[MemoValidationIssue]:
    """Предупреждение, если периодичность в тексте СЗ распознана неполностью."""
    if not document:
        return []

    from app.services.meeting_memo_recurrence import resolve_memo_recurrence

    header = document.get("header") or document.get("memo") or {}
    draft = resolve_memo_recurrence(header, document)
    if not draft.is_series:
        return []

    issues: list[MemoValidationIssue] = []
    if draft.confidence == "low":
        issues.append(
            MemoValidationIssue(
                field="series_planning",
                severity="warning",
                message=(
                    "Периодичность в тексте распознана неполностью; "
                    "уточните формулировку или выберите единоразовое совещание"
                ),
            )
        )
        return issues

    if draft.requires_user_choice:
        issues.append(
            MemoValidationIssue(
                field="series_planning",
                severity="info",
                message=(
                    f"В тексте указана серия совещаний: {draft.recurrence_label}. "
                    "Выберите «Запланировать серию» или «Единоразовое совещание»"
                ),
            )
        )
    return issues
