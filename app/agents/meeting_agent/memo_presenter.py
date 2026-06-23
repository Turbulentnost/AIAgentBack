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
from app.tools.onec.lookup_email_by_fio import dispatch_lookup_emails_by_fio

EMPTY_DATE_PREFIX = "0001-01-01"
ROOM_CATALOG = "Catalog_CRM_Помещения"
PRIORITY_CATALOG = "Catalog_Приоритеты"
GUID_PATTERN = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
STATUS_LABELS = {
    "НеСогласована": "Ожидает подтверждения УД",
    "Согласована": "Согласована",
}
MEETING_TYPE_LABELS = {
    "Плановое": "Плановое",
    "Внеплановое": "Внеплановое",
}


def is_empty_odata_date(value: str | None) -> bool:
    normalized = (value or "").strip()
    return not normalized or normalized.startswith(EMPTY_DATE_PREFIX)


def _looks_like_guid(value: str) -> bool:
    return bool(GUID_PATTERN.match(value.strip()))


def parse_odata_datetime(value: str | None) -> datetime | None:
    if is_empty_odata_date(value):
        return None
    normalized = value.strip()
    try:
        return datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError:
        return None


def parse_odata_time_component(value: str | None) -> tuple[int, int] | None:
    if not value or not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized or normalized.startswith(f"{EMPTY_DATE_PREFIX}T00:00:00"):
        return None
    try:
        dt = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError:
        return None
    if not normalized.startswith(EMPTY_DATE_PREFIX):
        return dt.hour, dt.minute
    if dt.hour == 0 and dt.minute == 0:
        return None
    return dt.hour, dt.minute


def resolve_meeting_schedule(header: dict[str, Any]) -> tuple[datetime | None, datetime | None]:
    meeting_day = (
        parse_odata_datetime(header.get("ДатаПроведенияСовещания"))
        or parse_odata_datetime(header.get("ЖелаемаяДатаПроведенияСовещания"))
    )

    start_raw = header.get("ВремяНачалаСовещания")
    end_raw = header.get("ВремяОкончанияСовещания")
    if start_raw and not is_empty_odata_date(start_raw):
        start = parse_odata_datetime(start_raw)
        end = parse_odata_datetime(end_raw) if end_raw and not is_empty_odata_date(end_raw) else None
        return start, end

    start_time = parse_odata_time_component(start_raw)
    end_time = parse_odata_time_component(end_raw)
    if meeting_day and start_time:
        start = meeting_day.replace(hour=start_time[0], minute=start_time[1], second=0, microsecond=0)
        end = None
        if end_time:
            end = meeting_day.replace(hour=end_time[0], minute=end_time[1], second=0, microsecond=0)
        return start, end
    return None, None


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


def _participant_ref_keys(row: dict[str, Any]) -> set[str]:
    keys: set[str] = set()
    for key in ("Участник_Key", "Сотрудник_Key"):
        value = row.get(key)
        if isinstance(value, str) and not is_empty_key(value):
            keys.add(value.strip())
    participant = row.get("Участник")
    if isinstance(participant, str) and not is_empty_key(participant):
        keys.add(participant.strip())
    elif isinstance(participant, dict):
        ref = participant.get("Ref_Key")
        if isinstance(ref, str) and not is_empty_key(ref):
            keys.add(ref.strip())
    return keys


def _is_participant_row(row: dict[str, Any]) -> bool:
    participant_key = row.get("Участник_Key")
    if isinstance(participant_key, str) and not is_empty_key(participant_key):
        return True
    if isinstance(row.get("Участник"), dict):
        return True
    return _participant_name(row) is not None


def _collect_participant_rows(document: dict[str, Any]) -> list[dict[str, Any]]:
    header = document.get("header") or document.get("memo") or {}
    rows: list[dict[str, Any]] = []

    inline = header.get("СписокУчастников")
    if isinstance(inline, list):
        rows.extend(item for item in inline if isinstance(item, dict) and _is_participant_row(item))

    for section_name, section_rows in (document.get("tabular_sections") or {}).items():
        if "Участник" not in section_name:
            continue
        if not isinstance(section_rows, list):
            continue
        for row in section_rows:
            if isinstance(row, dict) and _is_participant_row(row):
                rows.append(row)

    for participant in document.get("participants") or []:
        if isinstance(participant, dict) and _is_participant_row(participant):
            rows.append(participant)

    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        ref_keys = _participant_ref_keys(row)
        name = _participant_name(row)
        dedupe_key = next(iter(sorted(ref_keys)), None) or name
        if not dedupe_key or dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        deduped.append(row)
    return deduped


def _extract_participants_rows(document: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen_names: set[str] = set()
    for row in _collect_participant_rows(document):
        name = _participant_name(row)
        if not name or name in seen_names:
            continue
        seen_names.add(name)
        rows.append(row)
    return rows


def _participants_count_from_header(header: dict[str, Any]) -> int:
    inline = header.get("СписокУчастников")
    if isinstance(inline, list):
        return len(inline)
    return 0


def _resolve_participants(
    session: requests.Session,
    config: ODataConfig,
    document: dict[str, Any],
) -> list[dict[str, Any]]:
    rows = _collect_participant_rows(document)
    user_keys: set[str] = set()
    for row in rows:
        user_keys.update(_participant_ref_keys(row))

    users = _load_users_by_keys(session, config, user_keys)
    unresolved_keys = {key for key in user_keys if key not in users}
    persons = load_persons_for_keys(session, unresolved_keys, config=config)

    participants: list[dict[str, Any]] = []
    seen: set[str] = set()

    for row in rows:
        ref_keys = _participant_ref_keys(row)
        name = _participant_name(row)
        ref_key = next(iter(sorted(ref_keys)), None)

        if not name and ref_key:
            user = users.get(ref_key)
            if user:
                name = _clean_text(user.get("_resolved_fio") or user.get("Description"))
            else:
                person = persons.get(ref_key)
                if person:
                    name = _clean_text(person.get("Description") or person.get("ФИО"))

        if not name:
            continue
        if name in seen:
            continue
        seen.add(name)
        participants.append(
            {
                "full_name": name,
                "ref_key": ref_key or row.get("Ref_Key") or row.get("Участник_Key"),
                "department": _clean_text(
                    row.get("Подразделение") if isinstance(row.get("Подразделение"), str) else None
                ),
            }
        )

    return participants


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


def load_catalog_descriptions(
    session: requests.Session,
    config: ODataConfig,
    catalog: str,
    keys: set[str],
) -> dict[str, str]:
    normalized_keys = {
        key.strip()
        for key in keys
        if key and _looks_like_guid(key.strip()) and not is_empty_key(key)
    }
    if not normalized_keys:
        return {}

    result: dict[str, str] = {}
    chunk_size = 15
    key_list = sorted(normalized_keys)
    for offset in range(0, len(key_list), chunk_size):
        chunk = key_list[offset : offset + chunk_size]
        filter_expr = " or ".join(f"Ref_Key eq guid'{key}'" for key in chunk)
        url = (
            f"{entity_url(config.url, catalog)}"
            f"?$filter={quote(filter_expr, safe='')}"
            f"&$select={quote('Ref_Key,Description', safe=',_')}"
            f"&$format=json"
        )
        data = odata_get_json(session, url, timeout=config.timeout)
        for row in data.get("value") or []:
            ref_key = row.get("Ref_Key")
            description = _clean_text(row.get("Description"))
            if ref_key and description:
                result[str(ref_key).lower()] = description
    return result


def collect_location_keys(rows: list[dict[str, Any]]) -> set[str]:
    keys: set[str] = set()
    for row in rows:
        raw = row.get("МестоПроведенияСовещания")
        if isinstance(raw, str) and _looks_like_guid(raw):
            keys.add(raw.strip())
    return keys


def _resolve_location(
    header: dict[str, Any],
    location_labels: dict[str, str] | None = None,
) -> str | None:
    raw = header.get("МестоПроведенияСовещания")
    if isinstance(raw, dict):
        return _clean_text(raw.get("Description"))
    text = _clean_text(raw)
    if not text:
        return None
    if _looks_like_guid(text):
        labels = location_labels or {}
        return labels.get(text.lower()) or labels.get(text)
    return text


def _resolve_priority(
    header: dict[str, Any],
    priority_labels: dict[str, str] | None = None,
) -> str | None:
    raw = header.get("Приоритет")
    if isinstance(raw, dict):
        return _clean_text(raw.get("Description"))
    key = header.get("Приоритет_Key")
    if isinstance(key, str) and key.strip() and not is_empty_key(key):
        labels = priority_labels or {}
        resolved = labels.get(key.lower()) or labels.get(key)
        if resolved:
            return resolved
    text = _clean_text(raw)
    if text and not _looks_like_guid(text):
        return text
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


def _attach_cached_emails(
    application: dict[str, Any],
    *,
    config: ODataConfig,
) -> None:
    """Сохраняет e-mail в detail при прогреве через Exchange GAL (без CRM 1С)."""
    names: list[str] = []

    def collect(person: dict[str, Any] | None) -> None:
        if not person:
            return
        name = person.get("full_name")
        if isinstance(name, str) and name.strip() and not person.get("email"):
            names.append(name.strip())

    initiator = application.get("initiator")
    manager = application.get("manager")
    collect(initiator if isinstance(initiator, dict) else None)
    collect(manager if isinstance(manager, dict) else None)
    for participant in application.get("participants") or []:
        if isinstance(participant, dict):
            collect(participant)

    unique_names = list(dict.fromkeys(names))
    if not unique_names:
        return

    try:
        payload = dispatch_lookup_emails_by_fio(unique_names, config=config)
    except Exception:
        return

    by_fio: dict[str, str] = {}
    for item in payload.get("results") or []:
        fio = item.get("fio")
        emails = item.get("emails") or []
        if isinstance(fio, str) and emails:
            address = emails[0].get("email")
            if isinstance(address, str) and address.strip():
                by_fio[fio] = address.strip()

    def apply(person: dict[str, Any] | None) -> None:
        if not person or person.get("email"):
            return
        name = person.get("full_name")
        if isinstance(name, str) and name in by_fio:
            person["email"] = by_fio[name]

    apply(initiator if isinstance(initiator, dict) else None)
    apply(manager if isinstance(manager, dict) else None)
    for participant in application.get("participants") or []:
        if isinstance(participant, dict):
            apply(participant)


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
    start, end = resolve_meeting_schedule(header)
    meeting_date = start or (
        parse_odata_datetime(header.get("ДатаПроведенияСовещания"))
        or parse_odata_datetime(header.get("ЖелаемаяДатаПроведенияСовещания"))
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

    raw_location = header.get("МестоПроведенияСовещания")
    location = _resolve_location(header) or (
        _clean_text(raw_location) if isinstance(raw_location, str) and not _looks_like_guid(raw_location) else None
    )
    location_specified = bool(location) or (
        isinstance(raw_location, str) and bool(raw_location.strip()) and not is_empty_key(raw_location)
    )
    add(
        "location",
        "Переговорная указана",
        "warning" if not location_specified else "info",
        location or ("Место не указано" if not location_specified else "Место указано"),
        passed=location_specified,
    )

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


def _participant_names_from_header(
    header: dict[str, Any],
    *,
    session: requests.Session | None = None,
    config: ODataConfig | None = None,
) -> list[str]:
    document = {"header": header, "memo": header}
    if session is not None and config is not None:
        return [
            name
            for name in (
                participant.get("full_name")
                for participant in _resolve_participants(session, config, document)
            )
            if isinstance(name, str) and name.strip()
        ]

    names: list[str] = []
    for row in _collect_participant_rows(document):
        name = _participant_name(row)
        if name and name not in names:
            names.append(name)
    return names


def build_queue_item_from_row(
    row: dict[str, Any],
    *,
    location_labels: dict[str, str] | None = None,
    session: requests.Session | None = None,
    config: ODataConfig | None = None,
) -> dict[str, Any]:
    header = row
    start, end = resolve_meeting_schedule(header)
    doc_dt = parse_odata_datetime(header.get("Date"))
    participants = header.get("СписокУчастников") if isinstance(header.get("СписокУчастников"), list) else []
    participant_names = _participant_names_from_header(header, session=session, config=config)
    participants_count = max(
        len(participants) if participants else len(_collect_participant_rows({"header": header, "memo": header})),
        len(participant_names),
    )

    checks = _build_validation_checks({"memo": header, "header": header, "participants": participants}, participants_count=participants_count)
    warnings = _build_warnings(checks)
    location = _resolve_location(header, location_labels)

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
        "meeting_start": start.isoformat() if start else None,
        "meeting_end": end.isoformat() if end else None,
        "participants_count": participants_count,
        "participant_names": participant_names,
        "warnings": warnings,
        "location": location,
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

    participant_rows = _collect_participant_rows(document)
    participants = _resolve_participants(session, config, document)
    participants_count = max(len(participants), _participants_count_from_header(header), len(participant_rows))

    start, end = resolve_meeting_schedule(header)
    doc_dt = parse_odata_datetime(header.get("Date"))
    duration = _duration_minutes(start, end)

    location_keys = collect_location_keys([header])
    priority_key = header.get("Приоритет_Key")
    priority_keys = {priority_key} if isinstance(priority_key, str) and not is_empty_key(priority_key) else set()
    location_labels = load_catalog_descriptions(session, config, ROOM_CATALOG, location_keys)
    priority_labels = load_catalog_descriptions(session, config, PRIORITY_CATALOG, priority_keys)
    location = _resolve_location(header, location_labels)
    priority = _resolve_priority(header, priority_labels)

    checks = _build_validation_checks(document, participants_count=participants_count)
    warnings = _build_warnings(checks)

    queue = build_queue_item_from_row(
        header,
        location_labels=location_labels,
        session=session,
        config=config,
    )
    queue["participants_count"] = participants_count
    queue["warnings"] = warnings

    application = {
        "initiator": _person_from_user(manager_user),
        "manager": _person_from_user(manager_user),
        "participants": participants,
        "participants_count": participants_count,
        "agenda": _extract_agenda(header),
        "scheduled_label": _slot_label(start, end, fallback=doc_dt),
        "document_date": _clean_text(header.get("Date")),
        "meeting_start": start.isoformat() if start else None,
        "meeting_end": end.isoformat() if end else None,
        "duration_minutes": duration,
        "location": location,
        "meeting_type": header.get("ВидСовещания") or None,
        "meeting_type_label": _meeting_type_label(_clean_text(header.get("ВидСовещания"))),
        "priority": priority,
    }
    _attach_cached_emails(application, config=config)

    return {
        "ref_key": header.get("Ref_Key"),
        "number": header.get("Number"),
        "title": _extract_title(header),
        "status": header.get("Статус"),
        "status_label": _status_label(header.get("Статус")),
        "queue": queue,
        "application": application,
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
