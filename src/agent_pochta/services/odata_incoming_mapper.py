"""Маппинг EmailMessage + XML document → payload OData Document_ТД_ВходящаяКорреспонденция."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from agent_pochta.config import PROJECT_ROOT
from agent_pochta.routing.organizations import (
    DIRECTION_UNCLEAR,
    KS_PAYER_DIRECTION_DEPARTMENT_CODES,
    normalize_organization_code,
    resolve_organization_key_code,
)
from agent_pochta.routing.xml_builder import resolve_document_theme
from agent_pochta.routing.xml_parser import parse_document_xml
from agent_pochta.schemas import EmailMessage, RoutingResult

DEFAULT_PAYER_DIRECTION_MAP_FILE = (
    PROJECT_ROOT / "data" / "odata_payer_direction_map.json"
)
DEFAULT_INCOMING_DEFAULTS_FILE = PROJECT_ROOT / "data" / "odata_incoming_defaults.json"

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

DEFAULT_STATUS = "Подготовлен"
DEFAULT_SOURCE = "EMAIL"
DEFAULT_AUTHOR = "Искусственный интеллект 1С"
_MSK = ZoneInfo("Europe/Moscow")
_EMPTY_GUID = "00000000-0000-0000-0000-000000000000"
_COMPOSITE_STRING_TYPE = "Edm.String"
_ORG_AS_PAYER_DIRECTION = frozenset({"АЛ", "МГ", "АМ", "МИ", "БМ"})

# Enum 1С «ТД_НаправленияСлужебныхЗаписок» (поле OData «Направление»).
# Коды XML (<направление> ПР/КС/СС) сюда не входят — это отдельный enum «ПлательщикНаправление».
_VALID_ODATA_DIRECTIONS = frozenset(
    {
        "ГенеральныйДиректор",
        "ГлавныйМетролог",
        "ДиректорГлавныйКонструктор",
        "ДиректорМГС",
        "ДиректорНПО",
        "ДиректорПоКачеству",
        "ДиректорПоЛогистике",
        "ДиректорПоПерсоналу",
        "ДиректорПоПродажамКлючевымКлиентам",
        "ДиректорПоПроизводству",
        "ДиректорПоПроизводствуБМИ",
        "ДиректорПоРазвитию",
        "ДиректорПоРазвитиюПроизводства",
        "ДиректорПоСервисномуОбслуживанию",
        "ДиректорПроизводства1",
        "ДиректорПроизводства2",
        "ДиректорТорговогоДома",
        "ЗамКоммерческогоДиректора",
        "ЗаместительГлавногоКонструктораПоПроектнойДеятельности",
        "ЗаместительДиректораПоЭкономическойБезопасности",
        "ИПКорниенко",
        "ИсполнительныйДиректор",
        "КоммерческийДиректор",
        "НачальникСервиснойСлужбы",
        "ОперационныйДиректор",
        "ПрочиеВнутренние",
        "РевизионнаяКомиссия",
        "ТехническийДиректор",
        "УправлениеДелами",
        "ФинансовыйДиректор",
    }
)

_DEPARTMENT_ODATA_DIRECTION: dict[str, str] = {
    "00-000001": "ГенеральныйДиректор",
    "00-000152": "ОперационныйДиректор",
    "00-000182": "ОперационныйДиректор",
    "00-000066": "УправлениеДелами",
}

_XML_DIRECTION_TO_ODATA: dict[str, str] = {
    "ПР": "ДиректорПроизводства1",
    "КС": "ПрочиеВнутренние",
    "СС": "ДиректорПоСервисномуОбслуживанию",
    "МС": "ДиректорПоПроизводству",
    "РУ": "ДиректорПоРазвитию",
    "БМ": "ДиректорПоПроизводствуБМИ",
    "МГ": "ДиректорМГС",
}


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
    """GUID организации по коду из XML `<organization>` (НП, АЛ, …)."""
    code = resolve_organization_key_code(org_code)
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


def _as_msk(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=_MSK)
    return value.astimezone(_MSK)


def _format_odata_datetime(value: datetime) -> str:
    return _as_msk(value).replace(microsecond=0, tzinfo=None).strftime("%Y-%m-%dT%H:%M:%S")


def _odata_field(fields: dict[str, str], logical_key: str) -> str:
    return (fields.get(logical_key) or "").strip()


def load_incoming_defaults_from_file(path: str | Path) -> dict[str, Any]:
    """Читает доп. поля POST из JSON (data/odata_incoming_defaults.json)."""
    file_path = Path(path)
    if not file_path.is_file():
        return {}
    try:
        data = json.loads(file_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid incoming defaults file {file_path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"Incoming defaults file {file_path} must contain a JSON object")
    return dict(data)


def resolve_incoming_extra_fields(
    inline_json: str = "",
    *,
    file_path: str = "",
) -> dict[str, Any]:
    """Файл defaults + ODATA_INCOMING_EXTRA_FIELDS; inline перекрывает ключи из файла."""
    merged: dict[str, Any] = {}
    if file_path.strip():
        merged.update(load_incoming_defaults_from_file(file_path))
    if inline_json.strip():
        try:
            data = json.loads(inline_json)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid ODATA_INCOMING_EXTRA_FIELDS JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError("ODATA_INCOMING_EXTRA_FIELDS must be a JSON object")
        merged.update(data)
    return merged


def load_payer_direction_map(path: str | Path | None = None) -> dict[str, Any]:
    """Загружает org+direction → enum ПлательщикНаправление из JSON."""
    file_path = Path(path) if path else DEFAULT_PAYER_DIRECTION_MAP_FILE
    if not file_path.is_file():
        return {}
    try:
        data = json.loads(file_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid payer direction map file {file_path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"Payer direction map file {file_path} must contain a JSON object")
    return data


def resolve_payer_direction(
    org_code: str | None,
    direction_code: str | None,
    *,
    payer_direction_map: dict[str, Any] | None = None,
) -> str:
    """Преобразует organization + направление XML в enum 1С «ПлательщикНаправление»."""
    org = (org_code or "НП").strip().upper() or "НП"
    direction = (direction_code or "").strip().upper()
    if org in _ORG_AS_PAYER_DIRECTION and not direction:
        direction = org

    data = payer_direction_map if payer_direction_map is not None else load_payer_direction_map()
    organizations = data.get("organizations") if isinstance(data, dict) else None
    if not isinstance(organizations, dict):
        return "ТурбулентностьДОНКС"

    org_entry = organizations.get(org)
    if not isinstance(org_entry, dict):
        org_entry = organizations.get("НП")
    if not isinstance(org_entry, dict):
        return "ТурбулентностьДОНКС"

    directions = org_entry.get("directions")
    if isinstance(directions, dict) and direction:
        mapped = directions.get(direction)
        if isinstance(mapped, str) and mapped.strip():
            return mapped.strip()

    default = org_entry.get("default")
    if isinstance(default, str) and default.strip():
        return default.strip()

    np_entry = organizations.get("НП")
    if isinstance(np_entry, dict):
        np_default = np_entry.get("default")
        if isinstance(np_default, str) and np_default.strip():
            return np_default.strip()
    return "ТурбулентностьДОНКС"


def resolve_odata_direction(
    department_id: str | None,
    xml_direction: str | None,
) -> str:
    """Преобразует код отдела / XML-направление в enum OData «Направление».

    XML-коды ПР/КС/СС относятся к «ПлательщикНаправление», а не к этому полю.
    Невалидные значения не отправляются (пустая строка).
    """
    code = (department_id or "").strip()
    mapped = _DEPARTMENT_ODATA_DIRECTION.get(code)
    if mapped:
        return mapped

    direction = (xml_direction or "").strip()
    if not direction:
        return ""

    upper = direction.upper()
    if upper in _XML_DIRECTION_TO_ODATA:
        return _XML_DIRECTION_TO_ODATA[upper]

    if direction in _VALID_ODATA_DIRECTIONS:
        return direction

    return ""


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


def _put_composite_string(
    payload: dict[str, Any],
    fields: dict[str, str],
    logical_key: str,
    value: Any,
    *,
    max_len: int | None = None,
) -> None:
    """Составной строковый реквизит 1С OData (значение + *_Type)."""
    odata_key = _odata_field(fields, logical_key)
    if not odata_key:
        return
    text = str(value or "").strip()
    if not text:
        return
    if max_len is not None:
        text = text[:max_len]
    payload[odata_key] = text
    payload[f"{odata_key}_Type"] = _COMPOSITE_STRING_TYPE


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
    payer_direction_map: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Формирует тело POST для Document_ТД_ВходящаяКорреспонденция."""
    parsed = parse_document_xml(xml_document) if xml_document else None
    if not parsed:
        raise ValueError("xml_document is required for OData payload")

    fields = field_map or DEFAULT_FIELD_MAP

    mail_dt = _parse_mail_datetime(parsed.get("mail_datetime"), email.received_at)
    theme = resolve_document_theme(
        email,
        explicit_theme=parsed.get("theme") or "",
        combined_text=email.body_text or "",
        process_type=parsed.get("process") or "",
        claim=bool(parsed.get("claim")),
    )
    partner = parsed.get("partner") or ""
    if partner == "-":
        partner = ""
    xml_direction = (parsed.get("direction") or "").strip()
    payer_direction_code = xml_direction
    if (routing.department_id or "").strip() in KS_PAYER_DIRECTION_DEPARTMENT_CODES:
        payer_direction_code = DIRECTION_UNCLEAR
    odata_direction = resolve_odata_direction(routing.department_id, xml_direction)
    org_code = normalize_organization_code(parsed.get("organization")) or "НП"
    department_name = resolve_department_name(routing, department_names=department_names)
    department_key = resolve_department_key(routing.department_id, department_keys)
    organization_key = resolve_organization_key(org_code, organization_keys)
    payer_direction = resolve_payer_direction(
        org_code,
        payer_direction_code,
        payer_direction_map=payer_direction_map,
    )
    ai_task = summary_ru.strip() or str(theme).strip()

    payload: dict[str, Any] = {
        _odata_field(fields, "date"): _format_odata_datetime(mail_dt),
        _odata_field(fields, "source"): DEFAULT_SOURCE,
        _odata_field(fields, "status"): DEFAULT_STATUS,
    }
    payload = {key: value for key, value in payload.items() if key}

    _put_composite_string(payload, fields, "theme", theme, max_len=200)
    _put_composite_string(payload, fields, "department_name", department_name, max_len=200)
    _put_composite_string(payload, fields, "partner", partner, max_len=200)
    _put_composite_string(payload, fields, "payer", payer_direction, max_len=200)
    _put_string(payload, fields, "direction", odata_direction, max_len=50)
    _put_guid(payload, fields, "organization_key", organization_key)
    _put_guid(payload, fields, "department_executor_key", department_key)
    _put_guid(payload, fields, "department_assignee_key", department_key)

    _put_string(payload, fields, "ai_task_theme", ai_task, max_len=800)
    _put_composite_string(payload, fields, "author", DEFAULT_AUTHOR, max_len=100)
    _put_string(payload, fields, "assignee", routing.department_id, max_len=50)
    _put_string(payload, fields, "email_sender", email.sender_email, max_len=200)
    _put_string(
        payload,
        fields,
        "email_recipient",
        parsed.get("email_recipient") or email.routing_recipient or email.mailbox,
        max_len=200,
    )

    claim_key = _odata_field(fields, "claim")
    if claim_key:
        payload[claim_key] = bool(parsed.get("claim"))

    if extra_fields:
        payload.update(extra_fields)

    return payload
