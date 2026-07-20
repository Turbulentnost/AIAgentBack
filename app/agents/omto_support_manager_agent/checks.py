"""Pure mandatory-field checks for OMTO agent (no heavy imports)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.agents.omto_support_manager_agent.schemas import (
    FIELD_LABELS_RU,
    MANDATORY_FIELD_KEYS,
    OmtoFinding,
)

_REF_CFO = {"ЦФО-01", "ЦФО-02", "ЦФО-PROD", "ЦФО-ADMIN"}
_REF_ARTICLE = {"СТ-100", "СТ-210", "СТ-300", "СТ-450"}
_REF_PROJECT = {"PRJ-ALPHA", "PRJ-BETA", "PRJ-GAMMA", "PRJ-OPS"}
_REF_NOMENCLATURE = {"NOM-КР-12", "NOM-КАБ-5", "NOM-ПОДШ-8", "NOM-М-12"}


def _is_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and not value.strip():
        return True
    return False


def _parse_date(value: Any) -> bool:
    if _is_empty(value):
        return False
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y"):
        try:
            datetime.strptime(text, fmt)
            return True
        except ValueError:
            continue
    return False


def _parse_quantity(value: Any) -> bool:
    if _is_empty(value):
        return False
    try:
        return float(value) > 0
    except (TypeError, ValueError):
        return False


def evaluate_mandatory_fields(fields: dict[str, Any], record_id: str) -> list[OmtoFinding]:
    findings: list[OmtoFinding] = []
    checks: list[tuple[str, str, Any, bool, str, str]] = [
        (
            "cfo",
            "DQ.MANDATORY.CFO",
            fields.get("cfo"),
            (not _is_empty(fields.get("cfo"))) and str(fields.get("cfo")).strip() in _REF_CFO,
            "ЦФО не заполнен",
            "ЦФО отсутствует в справочнике",
        ),
        (
            "article",
            "DQ.MANDATORY.ARTICLE",
            fields.get("article"),
            (not _is_empty(fields.get("article")))
            and str(fields.get("article")).strip() in _REF_ARTICLE,
            "Статья не заполнена",
            "Статья отсутствует в справочнике",
        ),
        (
            "project",
            "DQ.MANDATORY.PROJECT",
            fields.get("project"),
            (not _is_empty(fields.get("project")))
            and str(fields.get("project")).strip() in _REF_PROJECT,
            "Проект не заполнен",
            "Проект отсутствует в справочнике",
        ),
        (
            "date",
            "DQ.MANDATORY.DATE",
            fields.get("date"),
            _parse_date(fields.get("date")),
            "Дата не заполнена",
            "Дата не распознана (ожидается ДД.ММ.ГГГГ или ГГГГ-ММ-ДД)",
        ),
        (
            "nomenclature",
            "DQ.MANDATORY.NOMENCLATURE",
            fields.get("nomenclature"),
            (not _is_empty(fields.get("nomenclature")))
            and str(fields.get("nomenclature")).strip() in _REF_NOMENCLATURE,
            "Номенклатура не заполнена",
            "Номенклатура отсутствует в справочнике",
        ),
        (
            "quantity",
            "DQ.MANDATORY.QUANTITY",
            fields.get("quantity"),
            _parse_quantity(fields.get("quantity")),
            "Количество не заполнено",
            "Количество должно быть числом > 0",
        ),
    ]
    for field, rule_id, value, ok, msg_empty, msg_invalid in checks:
        if ok:
            continue
        message = msg_empty if _is_empty(value) else f"{msg_invalid} [record={record_id}]"
        findings.append(
            OmtoFinding(
                field=field,
                rule_id=rule_id,
                source_ref=f"payload.fields.{field}",
                message=message,
                severity="critical",
                suggested_fix=f"Указать корректное значение поля «{FIELD_LABELS_RU[field]}»",
                current_value=value,
            )
        )
    return findings


__all__ = ["MANDATORY_FIELD_KEYS", "evaluate_mandatory_fields"]
