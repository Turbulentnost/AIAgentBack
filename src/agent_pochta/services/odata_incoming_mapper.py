"""Маппинг EmailMessage + XML document → payload OData Document_ТД_ВходящаяКорреспонденция."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from agent_pochta.routing.xml_builder import resolve_document_theme
from agent_pochta.routing.xml_parser import parse_document_xml
from agent_pochta.schemas import EmailMessage, Priority, RoutingResult

# Поля 1С по $metadata Document_ТД_ВходящаяКорреспонденция (переопределяются через ODATA_INCOMING_FIELD_MAP).
# OpenType=true: «Автор», «Подразделение» — пользовательские реквизиты карточки.
DEFAULT_FIELD_MAP: dict[str, str] = {
    "date": "Date",
    "source": "ИсточникПоступления",
    "organization_key": "Организация_Key",
    "theme": "ТемаСлужебнойЗаписки",
    "department_name": "Подразделение",
    "department_executor_key": "ПодразделениеИсполнитель_Key",
    "department_assignee_key": "КомуПодразделениеСсылка_Key",
    "partner": "Партнер",
    "payer": "ПлательщикНаправление",
    "direction": "Направление",
    "ai_task_theme": "Содержание",
    "author": "Автор",
    "assignee": "Кому",
    "document_basis": "ДокументОснование",
    "content": "Содержание",
    "claim": "Претензия",
    "email_sender": "EmailОтправителяПисьма",
    "email_recipient": "EmailПолучателяПисьма",
    "message_id": "ID_XML",
    "xml_result": "Комментарий",
    "status": "Статус",
    "mail_outgoing_date": "ДатаИсходящая",
}

DEFAULT_STATUS = "Передано на исполнение"
DEFAULT_SOURCE = "E-MAIL"
DEFAULT_AUTHOR = "ИИ 1С"
_COMMENT_MAX_LEN = 32_000
_EMPTY_GUID = "00000000-0000-0000-0000-000000000000"


def load_field_map(raw: str = "") -> dict[str, str]:
    """Объединяет DEFAULT_FIELD_MAP с JSON из ODATA_INCOMING_FIELD_MAP."""
    merged = dict(DEFAULT_FIELD_MAP)
    if not raw.strip():
        return merged
    try:
        overrides = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid ODATA_INCOMING_FIELD_MAP JSON: {exc}") from exc
    if not isinstance(overrides, dict):
        raise ValueError("ODATA_INCOMING_FIELD_MAP must be a JSON object")
    for key, value in overrides.items():
        if isinstance(key, str) and isinstance(value, str):
            merged[key] = value.strip()
    return merged


def load_guid_map(raw: str = "", *, env_name: str) -> dict[str, str]:
    """Загружает JSON-словарь код → GUID (ODATA_ORGANIZATION_KEYS / ODATA_DEPARTMENT_KEYS)."""
    if not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid {env_name} JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{env_name} must be a JSON object")
    return {
        str(code).strip(): str(guid).strip()
        for code, guid in data.items()
        if str(code).strip() and str(guid).strip()
    }


def load_guid_map_from_file(path: str | Path, *, env_name: str) -> dict[str, str]:
    """Читает код → GUID из JSON-файла (data/odata_*_keys.json)."""
    file_path = Path(path)
    if not file_path.is_file():
        return {}
    try:
        data = json.loads(file_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid {env_name} file {file_path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{env_name} file {file_path} must contain a JSON object")
    return {
        str(code).strip(): str(guid).strip()
        for code, guid in data.items()
        if str(code).strip() and str(guid).strip()
    }


def resolve_guid_map(
    inline_json: str = "",
    *,
    file_path: str = "",
    env_name: str,
) -> dict[str, str]:
    """Inline JSON из .env имеет приоритет над файлом."""
    inline = load_guid_map(inline_json, env_name=env_name)
    if inline:
        return inline
    if file_path.strip():
        return load_guid_map_from_file(file_path, env_name=env_name)
    return {}


def build_department_name_lookup(rules: dict[str, Any] | None) -> dict[str, str]:
    """Собирает code → name: приоритет у структуры 1С, затем routing_rules."""
    from agent_pochta.services.routing_departments import load_onec_department_names_map

    lookup: dict[str, str] = dict(load_onec_department_names_map())
    if not rules:
        return lookup
    for code, name in (rules.get("department_names") or {}).items():
        code = str(code).strip()
        name = str(name).strip()
        if code and name and code not in lookup:
            lookup[code] = name
    for bucket in (
        "email_keyword_rules",
        "exact_email_rules",
        "content_rules",
        "sender_rules",
    ):
        for rule in rules.get(bucket) or []:
            code = str(rule.get("code") or "").strip()
            name = str(rule.get("name") or rule.get("about") or "").strip()
            if code and name and code not in lookup:
                lookup.setdefault(code, name)
    reserve_code = str(rules.get("reserve_code") or "").strip()
    reserve_name = str(rules.get("reserve_name") or "").strip()
    if reserve_code and reserve_name:
        lookup.setdefault(reserve_code, reserve_name)
    return lookup


def resolve_department_name(
    routing: RoutingResult,
    *,
    department_names: dict[str, str] | None = None,
) -> str:
    """Название подразделения: структура 1С → routing → справочник routing_rules."""
    from agent_pochta.services.routing_departments import resolve_department_display_name

    fallback = (routing.department_name or "").strip()
    if not fallback and department_names:
        fallback = (department_names.get(routing.department_id) or "").strip()
    return resolve_department_display_name(routing.department_id, fallback)


def resolve_organization_key(
    org_code: str,
    organization_keys: dict[str, str] | None,
) -> str:
    code = (org_code or "НП").strip() or "НП"
    if not organization_keys:
        return ""
    return (organization_keys.get(code) or organization_keys.get(code.upper()) or "").strip()


def resolve_department_key(
    department_id: str,
    department_keys: dict[str, str] | None,
) -> str:
    code = (department_id or "").strip()
    if not code or not department_keys:
        return ""
    return (department_keys.get(code) or "").strip()


def _parse_mail_datetime(value: str | None, fallback: datetime) -> datetime:
    if not value:
        return fallback
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            return datetime.strptime(value.strip(), fmt)
        except ValueError:
            continue
    return fallback


def _format_odata_datetime(value: datetime) -> str:
    return value.replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%S")


def _priority_label(priority: Priority) -> str:
    return {
        Priority.URGENT: "срочный",
        Priority.HIGH: "высокий",
        Priority.NORMAL: "обычный",
    }.get(priority, priority.value)


def _odata_field(fields: dict[str, str], logical_key: str) -> str:
    return (fields.get(logical_key) or "").strip()


def _put_string(
    payload: dict[str, Any],
    fields: dict[str, str],
    logical_key: str,
    value: Any,
    *,
    max_len: int | None = None,
) -> None:
    odata_key = _odata_field(fields, logical_key)
    if not odata_key:
        return
    text = str(value or "").strip()
    if not text:
        return
    if max_len is not None:
        text = text[:max_len]
    payload[odata_key] = text


def _put_guid(
    payload: dict[str, Any],
    fields: dict[str, str],
    logical_key: str,
    guid: str,
) -> None:
    odata_key = _odata_field(fields, logical_key)
    value = (guid or "").strip()
    if not odata_key or not value or value == _EMPTY_GUID:
        return
    payload[odata_key] = value


def _build_comment(
    *,
    xml_document: str | None,
    parsed: dict[str, Any] | None,
    summary_ru: str,
    routing: RoutingResult,
) -> str:
    parts: list[str] = []
    if parsed:
        if parsed.get("confidence_level"):
            parts.append(f"Уверенность: {parsed['confidence_level']}")
        if parsed.get("matching_keywords"):
            parts.append(f"Ключевые слова: {parsed['matching_keywords']}")
        if parsed.get("processing_notes"):
            parts.append(f"Примечание: {parsed['processing_notes']}")
        if parsed.get("spam"):
            parts.append("Спам: да")
    parts.append(f"Приоритет: {_priority_label(routing.priority)}")
    parts.append(f"Отдел: {routing.department_id} — {routing.department_name}")
    if summary_ru.strip():
        parts.append(f"Обзор: {summary_ru.strip()}")
    if xml_document and xml_document.strip():
        parts.append("XML:")
        parts.append(xml_document.strip())
    comment = "\n".join(parts).strip()
    if len(comment) > _COMMENT_MAX_LEN:
        return comment[: _COMMENT_MAX_LEN - 3] + "..."
    return comment


def build_incoming_document_payload(
    email: EmailMessage,
    routing: RoutingResult,
    summary_ru: str,
    *,
    xml_document: str | None = None,
    field_map: dict[str, str] | None = None,
    extra_fields: dict[str, Any] | None = None,
    organization_keys: dict[str, str] | None = None,
    department_keys: dict[str, str] | None = None,
    department_names: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Формирует тело POST для Document_ТД_ВходящаяКорреспонденция."""
    fields = field_map or DEFAULT_FIELD_MAP
    parsed = parse_document_xml(xml_document) if xml_document else None

    mail_dt = _parse_mail_datetime(
        (parsed or {}).get("mail_datetime"),
        email.received_at,
    )
    theme = resolve_document_theme(
        email,
        explicit_theme=(parsed or {}).get("theme") or "",
        combined_text=email.body_text or "",
        process_type=(parsed or {}).get("process") or "",
        claim=bool((parsed or {}).get("claim")),
    )
    partner = (parsed or {}).get("partner") or ""
    if partner == "-":
        partner = ""
    payer = (parsed or {}).get("payer") or partner
    direction = (parsed or {}).get("direction") or ""
    org_code = (parsed or {}).get("organization") or "НП"
    department_name = resolve_department_name(routing, department_names=department_names)
    department_key = resolve_department_key(routing.department_id, department_keys)
    organization_key = resolve_organization_key(org_code, organization_keys)
    ai_task = summary_ru.strip() or str(theme).strip()

    payload: dict[str, Any] = {
        _odata_field(fields, "date"): _format_odata_datetime(mail_dt),
        _odata_field(fields, "source"): DEFAULT_SOURCE,
        _odata_field(fields, "message_id"): email.message_id,
        _odata_field(fields, "status"): DEFAULT_STATUS,
        _odata_field(fields, "mail_outgoing_date"): _format_odata_datetime(mail_dt),
    }
    payload = {key: value for key, value in payload.items() if key}

    # Обязательные поля
    _put_string(payload, fields, "theme", theme, max_len=200)
    _put_string(payload, fields, "department_name", department_name, max_len=200)
    _put_string(payload, fields, "partner", partner, max_len=200)
    _put_string(payload, fields, "payer", payer, max_len=200)
    _put_string(payload, fields, "direction", direction, max_len=50)
    _put_guid(payload, fields, "organization_key", organization_key)
    _put_guid(payload, fields, "department_executor_key", department_key)
    _put_guid(payload, fields, "department_assignee_key", department_key)

    # Необязательные поля
    _put_string(payload, fields, "ai_task_theme", ai_task, max_len=800)
    _put_string(payload, fields, "author", DEFAULT_AUTHOR, max_len=100)
    _put_string(payload, fields, "assignee", routing.department_id, max_len=50)
    _put_string(payload, fields, "document_basis", email.subject, max_len=500)

    _put_string(
        payload,
        fields,
        "email_sender",
        email.sender_email,
        max_len=200,
    )
    _put_string(
        payload,
        fields,
        "email_recipient",
        (parsed or {}).get("email_recipient") or email.routing_recipient or email.mailbox,
        max_len=200,
    )

    if parsed:
        claim_key = _odata_field(fields, "claim")
        if claim_key:
            payload[claim_key] = bool(parsed.get("claim"))

    comment_key = _odata_field(fields, "xml_result")
    if comment_key:
        payload[comment_key] = _build_comment(
            xml_document=xml_document,
            parsed=parsed,
            summary_ru=summary_ru,
            routing=routing,
        )

    if extra_fields:
        payload.update(extra_fields)

    return payload
