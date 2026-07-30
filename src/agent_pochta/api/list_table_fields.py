"""Поля list API для табличного вида «Таняфикация» (колонки как в 1С)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from agent_pochta.config import PROJECT_ROOT
from agent_pochta.routing.organizations import (
    ORG_FULL_NAMES,
    normalize_organization_code,
    resolve_direction_for_department,
)
from agent_pochta.routing.xml_parser import parse_document_xml
from agent_pochta.services.odata_incoming_mapper import (
    DEFAULT_INCOMING_DEFAULTS_FILE,
    resolve_payer_direction,
)

DEFAULT_PAYER_DIRECTION_DISPLAY_FILE = (
    PROJECT_ROOT / "data" / "odata_payer_direction_display.json"
)
DEFAULT_DEFAULTS_DISPLAY_FILE = PROJECT_ROOT / "data" / "odata_defaults_display.json"
_MSK = ZoneInfo("Europe/Moscow")


@lru_cache(maxsize=1)
def load_payer_direction_display_map() -> dict[str, str]:
    path = DEFAULT_PAYER_DIRECTION_DISPLAY_FILE
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return {str(key): str(value) for key, value in data.items()} if isinstance(data, dict) else {}


@lru_cache(maxsize=1)
def load_defaults_display() -> dict[str, Any]:
    path = DEFAULT_DEFAULTS_DISPLAY_FILE
    if not path.is_file():
        return {"access_labels": {}, "responsible_labels": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"access_labels": {}, "responsible_labels": {}}
    return data if isinstance(data, dict) else {"access_labels": {}, "responsible_labels": {}}


@lru_cache(maxsize=1)
def load_incoming_defaults() -> dict[str, Any]:
    if not DEFAULT_INCOMING_DEFAULTS_FILE.is_file():
        return {}
    try:
        data = json.loads(DEFAULT_INCOMING_DEFAULTS_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def payer_direction_display_label(enum_value: str) -> str:
    mapped = load_payer_direction_display_map().get(enum_value)
    return mapped if mapped else enum_value


def default_access_label() -> str:
    defaults = load_incoming_defaults()
    display = load_defaults_display()
    access_labels = display.get("access_labels") if isinstance(display.get("access_labels"), dict) else {}
    key = str(defaults.get("ГрифДоступа_Key") or "").strip().lower()
    if key and isinstance(access_labels, dict):
        for guid, label in access_labels.items():
            if str(guid).strip().lower() == key:
                return str(label)
    return "Общий"


_AI_RESPONSIBLE_MARKERS = (
    "ии 1с",
    "ии-агент",
    "ии агент",
    "искусственный интеллект",
)


def _is_ai_responsible_label(label: str) -> bool:
    normalized = label.strip().casefold().replace("ё", "е")
    if not normalized or normalized in ("—", "-"):
        return False
    if normalized == "ии":
        return True
    return any(marker in normalized for marker in _AI_RESPONSIBLE_MARKERS)


def _fallback_human_responsible_label(responsible_labels: dict[str, Any]) -> str:
    display = load_defaults_display()
    configured = display.get("default_responsible_label")
    if isinstance(configured, str) and configured.strip():
        return configured.strip()
    for label in responsible_labels.values():
        text = str(label).strip()
        if text and not _is_ai_responsible_label(text):
            return text
    return "Донченко Вера И."


def default_responsible_label() -> str:
    defaults = load_incoming_defaults()
    display = load_defaults_display()
    responsible_labels = (
        display.get("responsible_labels")
        if isinstance(display.get("responsible_labels"), dict)
        else {}
    )
    key = str(defaults.get("Ответственный_Key") or "").strip().lower()
    if key and isinstance(responsible_labels, dict):
        for guid, label in responsible_labels.items():
            if str(guid).strip().lower() == key:
                resolved = str(label).strip()
                if _is_ai_responsible_label(resolved):
                    return _fallback_human_responsible_label(responsible_labels)
                return resolved
    if isinstance(responsible_labels, dict) and responsible_labels:
        return _fallback_human_responsible_label(responsible_labels)
    return "—"


def _parse_mail_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    normalized = text.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


def _mail_date_from_received_at(received_at: datetime | None) -> str | None:
    """Дата письма для UI: received_at (naive UTC в БД) → Europe/Moscow."""
    if received_at is None:
        return None
    dt = received_at
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    msk = dt.astimezone(_MSK)
    return msk.replace(tzinfo=None, microsecond=0).isoformat(timespec="seconds")


def attachments_summary_from_row(row, payload: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    db_atts = list(getattr(row, "attachments", None) or [])
    if db_atts:
        for index, att in enumerate(db_atts):
            filename = str(getattr(att, "filename", "") or "").strip()
            if filename:
                items.append({"index": index, "filename": filename})
        return items

    for index, item in enumerate(payload.get("attachments") or []):
        if not isinstance(item, dict):
            continue
        filename = str(item.get("filename") or "").strip()
        if filename:
            items.append({"index": index, "filename": filename})
    return items


def operator_review_state(
    payload: dict[str, Any],
    *,
    has_operator_approve: bool = False,
    has_operator_change: bool = False,
) -> str:
    """Состояние проверки: payload-флаги + inference из classification_events."""
    if bool(payload.get("operator_corrected")) or has_operator_change:
        return "corrected"
    if bool(payload.get("operator_verified")) or has_operator_approve:
        return "verified"
    return "pending"


def dialog_category_fields(payload: dict[str, Any], *, status: str = "") -> dict[str, Any]:
    """Категория «Диалог» для табличного UI."""
    dialog = payload.get("dialog") if isinstance(payload.get("dialog"), dict) else {}
    routing_decision = (
        payload.get("routing_decision") if isinstance(payload.get("routing_decision"), dict) else {}
    )
    document_kind = str(
        dialog.get("document_kind")
        or routing_decision.get("document_kind")
        or ""
    ).strip()
    dialog_mode = dialog.get("mode")
    is_dialog = bool(dialog) or document_kind == "dialog" or status == "dialog"
    category_label = "Диалог" if is_dialog else None
    return {
        "is_dialog": is_dialog,
        "document_kind": document_kind or None,
        "document_category_label": category_label,
        "dialog_mode": dialog_mode,
    }


def row_to_table_fields(
    row,
    *,
    payload: dict[str, Any] | None = None,
    operator_event_hints: dict[str, bool] | None = None,
) -> dict[str, Any]:
    """Дополнительные поля list API для таблицы 1С."""
    if payload is None:
        if not row.raw_payload_json:
            payload = {}
        else:
            try:
                payload = json.loads(row.raw_payload_json)
            except json.JSONDecodeError:
                payload = {}
        if not isinstance(payload, dict):
            payload = {}

    xml_raw = payload.get("xml_document")
    document_xml = (
        parse_document_xml(str(xml_raw))
        if isinstance(xml_raw, str) and xml_raw.strip()
        else None
    ) or {}

    org_code = normalize_organization_code(str(document_xml.get("organization") or "")) or "НП"
    direction = resolve_direction_for_department(
        row.department_id or "",
        org_code,
        fallback_direction=str(document_xml.get("direction") or "") or None,
    )
    payer_enum = resolve_payer_direction(org_code, direction)

    # received_at — единственный надёжный источник; XML mail_datetime может содержать
    # локальное время отправителя из Date-заголовка, а не MSK.
    mail_date = _mail_date_from_received_at(row.received_at)
    if mail_date is None:
        mail_dt = _parse_mail_datetime(document_xml.get("mail_datetime"))
        if mail_dt is not None:
            mail_date = mail_dt.replace(tzinfo=None, microsecond=0).isoformat(
                timespec="seconds"
            )

    return {
        "mail_date": mail_date,
        "organization": org_code,
        "organization_name": ORG_FULL_NAMES.get(org_code, org_code),
        "direction": direction,
        "payer_direction_label": payer_direction_display_label(payer_enum),
        "access_label": default_access_label(),
        "responsible_label": default_responsible_label(),
        "attachments_summary": attachments_summary_from_row(row, payload),
        "operator_review_state": operator_review_state(
            payload,
            has_operator_approve=bool((operator_event_hints or {}).get("has_operator_approve")),
            has_operator_change=bool((operator_event_hints or {}).get("has_operator_change")),
        ),
        **dialog_category_fields(payload, status=str(getattr(row, "status", "") or "")),
    }
