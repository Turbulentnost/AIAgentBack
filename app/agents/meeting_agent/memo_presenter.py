from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any
from urllib.parse import quote

import requests

from app.agents.meeting_agent.memo_validation import validate_meeting_memo_document
from app.tools.onec.connection import ODataConfig
from app.tools.onec.get_meetings import (
    DOCUMENT_ENTITY,
    enrich_document,
    entity_url,
    load_metadata_xml,
    odata_get_json,
    tabular_entities_from_metadata,
)
from app.tools.onec.lookup_user_ref import USER_CATALOG, is_empty_key, load_persons_for_keys, user_fio

EMPTY_DATE = "0001-01-01T00:00:00"
STATUS_LABELS = {
    "НеСогласована": "Ожидает подтверждения УД",
    "Согласована": "Согласована",
}
MEETING_TYPE_LABELS = {
    "Плановое": "Плановое",
    "Внеплановое": "Внеплановое",
}


def parse_odata_datetime(value: str | None) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized or normalized.startswith(EMPTY_DATE):
        return None
    try:
        return datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError:
        return None


def _clean_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _extract_title(header: dict[str, Any]) -> str | None:
    for key in ("ТемаСовещания", "Комментарий"):
        text = _clean_text(header.get(key))
        if text:
            return text
    body = _clean_text(header.get("ТекстСлужебнойЗаписки"))
    if not body:
        return None
    first_line = body.splitlines()[0].strip()
    first_line = re.sub(r'^[\s"«»]+|[\s"«»]+$', "", first_line)
    return first_line or body[:160]


def _extract_agenda(header: dict[str, Any]) -> str | None:
    return _clean_text(header.get("ТекстСлужебнойЗаписки")) or _extract_title(header)


def _meeting_type_label(raw: str | None) -> str | None:
    if not raw:
        return None
    return MEETING_TYPE_LABELS.get(raw.strip(), raw.strip())


def _status_label(raw: str | None) -> str | None:
    if not raw:
        return None
    return STATUS_LABELS.get(raw.strip(), raw.strip())


def _duration_minutes(start: datetime | None, end: datetime | None) -> int | None:
    if start is None or end is None:
        return None
    minutes = int((end - start).total_seconds() // 60)
    return minutes if minutes > 0 else None


def _slot_label(start: datetime | None, end: datetime | None, fallback: datetime | None = None) -> str | None:
    point = start or fallback
    if point is None:
        return None
    if end is not None and start is not None and start.date() == end.date():
        return f"{start.strftime('%d.%m.%Y')}, {start.strftime('%H:%M')}–{end.strftime('%H:%M')}"
    return point.strftime("%d.%m.%Y, %H:%M")


def _extract_participants_rows(document: dict[str, Any]) -> list[dict[str, Any]]:
    header = document.get("header") or document.get("memo") or {}
    rows: list[dict[str, Any]] = []

    inline = header.get("СписокУчастников")
    if isinstance(inline, list):
        rows.extend(item for item in inline if isinstance(item, dict))

    for section_rows in (document.get("tabular_sections") or {}).values():
        if not isinstance(section_rows, list):
            continue
        for row in section_rows:
            if isinstance(row, dict):
                rows.append(row)

    for participant in document.get("participants") or []:
        if isinstance(participant, dict):
            rows.append(participant)

    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        name = _participant_name(row)
        if not name or name in seen:
            continue
        seen.add(name)
        deduped.append(row)
    return deduped


def _participant_name(row: dict[str, Any]) -> str | None:
    for key in ("Description", "ФИО", "Participant", "Участник", "Сотрудник"):
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, dict):
            nested = value.get("Description") or value.get("ФИО")
            if isinstance(nested, str) and nested.strip():
                return nested.strip()
    return None


def _load_users_by_keys(
    session: requests.Session,
    config: ODataConfig,
    user_keys: set[str],
) -> dict[str, dict[str, Any]]:
    keys = [key for key in user_keys if not is_empty_key(key)]
    if not keys:
        return {}

    result: dict[str, dict[str, Any]] = {}
    chunk_size = 15
    for offset in range(0, len(keys), chunk_size):
        chunk = keys[offset : offset + chunk_size]
        filter_expr = " or ".join(f"Ref_Key eq guid'{key}'" for key in chunk)
        url = (
            f"{entity_url(config.url, USER_CATALOG)}"
            f"?$filter={quote(filter_expr, safe='')}"
            f"&$format=json"
        )
        data = odata_get_json(session, url, timeout=config.timeout)
        for row in data.get("value") or []:
            if row.get("Ref_Key"):
                result[row["Ref_Key"]] = row

    person_keys = {
        row.get("ФизическоеЛицо_Key")
        for row in result.values()
        if row.get("ФизическоеЛицо_Key") and not is_empty_key(row.get("ФизическоеЛицо_Key"))
    }
    persons = load_persons_for_keys(session, person_keys, config=config)
    for row in result.values():
        row["_resolved_fio"] = user_fio(row, persons) or row.get("Description")
    return result


def _person_from_user(user: dict[str, Any] | None) -> dict[str, Any] | None:
    if not user:
        return None
    return {
        "ref_key": user.get("Ref_Key"),
        "full_name": user.get("_resolved_fio") or user.get("Description"),
        "department": None,
        "position": None,
    }


def _build_validation_checks(
    document: dict[str, Any],
    *,
    participants_count: int,
) -> list[dict[str, Any]]:
    issues = validate_meeting_memo_document(document)
    checks: list[dict[str, Any]] = []

    def add(field: str, label: str, severity: str, message: str, *, passed: bool) -> None:
        checks.append(
            {
                "field": field,
                "label": label,
                "severity": severity,
                "message": message,
                "passed": passed,
            }
        )

    header = document.get("header") or document.get("memo") or {}
    theme = _clean_text(header.get("ТемаСлужебнойЗаписки"))
    add(
        "theme",
        "Тема СЗ корректна",
        "info",
        theme or "Тема не указана",
        passed=bool(theme),
    )
    direction = _clean_text(header.get("Направление"))
    add(
        "direction",
        "Направление: управление делами",
        "info",
        direction or "Направление не указано",
        passed=bool(direction),
    )

    manager_key = header.get("Ответственный_Key") or header.get("РуководительСовещания_Key")
    add(
        "manager",
        "Руководитель найден в справочнике",
        "info" if manager_key and not is_empty_key(manager_key) else "warning",
        "Руководитель указан" if manager_key and not is_empty_key(manager_key) else "Руководитель не указан",
        passed=bool(manager_key and not is_empty_key(manager_key)),
    )

    add(
        "participants",
        "Участники найдены и активны",
        "error" if participants_count == 0 else "info",
        "Участники не указаны" if participants_count == 0 else f"Участников: {participants_count}",
        passed=participants_count > 0,
    )
    add(
        "participants_count",
        f"Количество участников: {participants_count}",
        "warning" if participants_count > 5 else "info",
        "Больше 5 участников" if participants_count > 5 else "Количество участников в норме",
        passed=participants_count <= 5,
    )

    memo_date = parse_odata_datetime(header.get("Date"))
    meeting_date = (
        parse_odata_datetime(header.get("ДатаПроведенияСовещания"))
        or parse_odata_datetime(header.get("ЖелаемаяДатаПроведенияСовещания"))
        or parse_odata_datetime(header.get("ВремяНачалаСовещания"))
    )
    deadline_ok = True
    deadline_message = "Срок подачи в норме"
    if memo_date and meeting_date and meeting_date.date() < memo_date.date():
        deadline_ok = False
        deadline_message = "Нарушен срок подачи"
    add(
        "submission_deadline",
        "Срок подачи: 1 раб. день",
        "warning" if not deadline_ok else "info",
        deadline_message,
        passed=deadline_ok,
    )

    location = _clean_text(header.get("МестоПроведенияСовещания"))
    add(
        "location",
        "Переговорная указана",
        "warning" if not location else "info",
        location or "Место не указано",
        passed=bool(location),
    )

    start = parse_odata_datetime(header.get("ВремяНачалаСовещания"))
    end = parse_odata_datetime(header.get("ВремяОкончанияСовещания"))
    duration = _duration_minutes(start, end)
    add(
        "duration",
        "Длительность соответствует типу",
        "warning" if duration is None else "info",
        f"{duration} минут" if duration else "Длительность не указана",
        passed=duration is not None,
    )

    for issue in issues:
        if any(check["field"] == issue.field for check in checks):
            continue
        checks.append(
            {
                "field": issue.field,
                "label": issue.field,
                "severity": issue.severity,
                "message": issue.message,
                "passed": issue.severity != "error",
            }
        )
    return checks


def _build_warnings(checks: list[dict[str, Any]]) -> list[str]:
    warnings: list[str] = []
    for check in checks:
        if check.get("passed"):
            continue
        if check.get("field") == "participants_count":
            warnings.append("Больше 5 участников")
        elif check.get("field") == "submission_deadline":
            warnings.append("Нарушен срок подачи")
        elif check.get("message"):
            warnings.append(str(check["message"]))
    return list(dict.fromkeys(warnings))


def build_queue_item_from_row(row: dict[str, Any]) -> dict[str, Any]:
    header = row
    start = parse_odata_datetime(header.get("ВремяНачалаСовещания"))
    end = parse_odata_datetime(header.get("ВремяОкончанияСовещания"))
    doc_dt = parse_odata_datetime(header.get("Date"))
    participants = header.get("СписокУчастников") if isinstance(header.get("СписокУчастников"), list) else []
    participants_count = len(participants)

    checks = _build_validation_checks({"memo": header, "header": header, "participants": participants}, participants_count=participants_count)
    warnings = _build_warnings(checks)

    return {
        "ref_key": header.get("Ref_Key"),
        "number": header.get("Number"),
        "title": _extract_title(header),
        "status": header.get("Статус"),
        "status_label": _status_label(header.get("Статус")),
        "meeting_type": header.get("ВидСовещания") or None,
        "meeting_type_label": _meeting_type_label(_clean_text(header.get("ВидСовещания"))),
        "document_date": _clean_text(header.get("Date")),
        "scheduled_label": _slot_label(start, end, fallback=doc_dt),
        "meeting_date": _clean_text(header.get("ДатаПроведенияСовещания")),
        "desired_meeting_date": _clean_text(header.get("ЖелаемаяДатаПроведенияСовещания")),
        "meeting_start": _clean_text(header.get("ВремяНачалаСовещания")),
        "meeting_end": _clean_text(header.get("ВремяОкончанияСовещания")),
        "participants_count": participants_count,
        "warnings": warnings,
        "location": _clean_text(header.get("МестоПроведенияСовещания")),
        "comment": _clean_text(header.get("Комментарий")),
        "subject": _extract_title(header),
    }


def build_memo_detail(
    session: requests.Session,
    config: ODataConfig,
    ref_key: str,
) -> dict[str, Any]:
    metadata = load_metadata_xml(session, config)
    tabular_entities = tabular_entities_from_metadata(metadata, DOCUMENT_ENTITY)
    document = enrich_document(
        session,
        config,
        {"Ref_Key": ref_key},
        tabular_entities,
        include_full_header=True,
    )
    header = document.get("header") or document.get("memo") or {}

    user_keys = {
        key
        for key in (
            header.get("Ответственный_Key"),
            header.get("РуководительСовещания_Key"),
        )
        if key and not is_empty_key(key)
    }
    users = _load_users_by_keys(session, config, user_keys)
    manager_user = users.get(header.get("РуководительСовещания_Key")) or users.get(header.get("Ответственный_Key"))

    participant_rows = _extract_participants_rows(document)
    participants = [
        {
            "full_name": _participant_name(row),
            "ref_key": row.get("Ref_Key") or row.get("Участник_Key"),
            "department": _clean_text(row.get("Подразделение") if isinstance(row.get("Подразделение"), str) else None),
        }
        for row in participant_rows
        if _participant_name(row)
    ]

    start = parse_odata_datetime(header.get("ВремяНачалаСовещания"))
    end = parse_odata_datetime(header.get("ВремяОкончанияСовещания"))
    doc_dt = parse_odata_datetime(header.get("Date"))
    duration = _duration_minutes(start, end)

    checks = _build_validation_checks(document, participants_count=len(participants))
    warnings = _build_warnings(checks)

    queue = build_queue_item_from_row(header)
    queue["participants_count"] = len(participants)
    queue["warnings"] = warnings

    return {
        "ref_key": header.get("Ref_Key"),
        "number": header.get("Number"),
        "title": _extract_title(header),
        "status": header.get("Статус"),
        "status_label": _status_label(header.get("Статус")),
        "queue": queue,
        "application": {
            "initiator": _person_from_user(manager_user),
            "manager": _person_from_user(manager_user),
            "participants": participants,
            "participants_count": len(participants),
            "agenda": _extract_agenda(header),
            "scheduled_label": _slot_label(start, end, fallback=doc_dt),
            "document_date": _clean_text(header.get("Date")),
            "meeting_start": _clean_text(header.get("ВремяНачалаСовещания")),
            "meeting_end": _clean_text(header.get("ВремяОкончанияСовещания")),
            "duration_minutes": duration,
            "location": _clean_text(header.get("МестоПроведенияСовещания")),
            "meeting_type": header.get("ВидСовещания") or None,
            "meeting_type_label": _meeting_type_label(_clean_text(header.get("ВидСовещания"))),
            "priority": None,
        },
        "validation_checks": checks,
        "warnings": warnings,
        "history": _build_history(header),
        "agent_recommendation": _agent_recommendation(checks, warnings),
    }


def _build_history(header: dict[str, Any]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    doc_dt = parse_odata_datetime(header.get("Date"))
    if doc_dt:
        events.append(
            {
                "timestamp": doc_dt.isoformat(),
                "message": "СЗ получена из 1C ERP",
            }
        )
        events.append(
            {
                "timestamp": (doc_dt + timedelta(minutes=1)).isoformat(),
                "message": "Проверены обязательные поля",
            }
        )
    status = header.get("Статус")
    if status == "НеСогласована" and doc_dt:
        events.append(
            {
                "timestamp": (doc_dt + timedelta(minutes=2)).isoformat(),
                "message": "Заявка передана УД",
            }
        )
    return events


def _agent_recommendation(checks: list[dict[str, Any]], warnings: list[str]) -> str | None:
    failed = [check for check in checks if not check.get("passed")]
    if not failed and not warnings:
        return "Заявка готова к подтверждению УД."
    if warnings:
        return "Рекомендуется проверить предупреждения перед подтверждением: " + "; ".join(warnings[:3])
    return "Есть ошибки проверки — требуется доработка заявки."
