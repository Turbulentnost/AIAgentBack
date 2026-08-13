"""TEMP/Aveon: чтение актуального утвержденного плана производства из 1C OData."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from urllib.parse import quote
import xml.etree.ElementTree as ET

import requests

from app.integrations.onec_odata import (
    create_session,
    format_onec_request_error,
    get_json,
    get_odata_base_url,
    get_request_timeout,
)

DOCUMENT_ENTITY = "Document_ПланПроизводства"
EMPTY_GUID = "00000000-0000-0000-0000-000000000000"
PAGE_SIZE = 200
LOOKUP_BATCH = 40
FALLBACK_TABLE_ENTITIES = [
    "Document_ПланПроизводства_Продукция",
    "Document_ПланПроизводства_ПродукцияПлан",
    "Document_ПланПроизводства_ВыпускПродукции",
    "Document_ПланПроизводства_План",
    "Document_ПланПроизводства_Материалы",
    "Document_ПланПроизводства_Товары",
]


def _fetch_metadata(session: requests.Session, base: str) -> str:
    response = session.get(
        f"{base}/$metadata",
        timeout=get_request_timeout(),
        headers={"Accept": "application/xml,text/xml"},
    )
    response.encoding = "utf-8"
    if not response.ok:
        raise RuntimeError(f"HTTP {response.status_code}: {(response.text or '')[:500]}")
    return response.text


def _metadata_entity_sets(metadata_xml: str) -> set[str]:
    root = ET.fromstring(metadata_xml)
    result: set[str] = set()
    for element in root.iter():
        if element.tag.endswith("EntitySet"):
            name = element.attrib.get("Name")
            if name:
                result.add(name)
    return result


def _production_plan_table_entities(entity_sets: set[str]) -> list[str]:
    prefix = f"{DOCUMENT_ENTITY}_"
    candidates = [
        name
        for name in entity_sets
        if name.startswith(prefix) and "ДополнительныеРеквизиты" not in name
    ]

    def priority(name: str) -> tuple[int, str]:
        lower = name.casefold()
        if "продукц" in lower:
            return (0, name)
        if "товар" in lower or "издел" in lower or "план" in lower:
            return (1, name)
        return (2, name)

    return sorted(candidates, key=priority)


def _candidate_table_entities(entity_sets: set[str] | None = None) -> list[str]:
    metadata_candidates = _production_plan_table_entities(entity_sets or set())
    return list(dict.fromkeys([*metadata_candidates, *FALLBACK_TABLE_ENTITIES]))


def _parse_odata_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.startswith("0001-01-01"):
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _format_odata_datetime(value: object) -> str:
    parsed = _parse_odata_datetime(value)
    if parsed is None:
        return str(value or "")
    return parsed.strftime("%d.%m.%Y %H:%M:%S")


def _is_approved_header(row: dict[str, Any]) -> bool:
    if row.get("DeletionMark") is True:
        return False
    if row.get("Posted") is False:
        return False
    # В ERP часто "утверждение" выражено проведением документа. Если в базе есть
    # явный статус, не отбрасываем проведенный документ только из-за другого имени enum.
    for key in ("Статус", "Состояние", "СтатусДокумента"):
        value = str(row.get(key) or "").casefold().replace("ё", "е")
        if value and "отмен" in value:
            return False
    return True


def _fetch_latest_approved_header(session: requests.Session, base: str) -> dict[str, Any] | None:
    # Не используем server-side `$filter=Posted eq true ...`: на больших базах 1С
    # такой запрос часто строит тяжёлый план. Берём последние документы и фильтруем тут.
    skip = 0
    select = "Ref_Key,Number,Date,Posted,DeletionMark"
    while skip < 500:
        url = (
            f"{base}/{DOCUMENT_ENTITY}"
            f"?$orderby={quote('Date desc', safe='')}"
            f"&$select={quote(select, safe=',')}"
            f"&$top=50&$skip={skip}&$format=json"
        )
        rows = get_json(session, url).get("value") or []
        approved = [row for row in rows if isinstance(row, dict) and _is_approved_header(row)]
        if approved:
            return max(
                approved,
                key=lambda row: _parse_odata_datetime(row.get("Date")) or datetime.min,
            )
        if len(rows) < 50:
            break
        skip += 50
    return None


def _fetch_plan_rows(session: requests.Session, base: str, entity: str, ref_key: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    skip = 0
    filters = f"Ref_Key eq guid'{ref_key}'"
    while True:
        url = (
            f"{base}/{entity}"
            f"?$filter={quote(filters, safe='')}"
            f"&$top={PAGE_SIZE}&$skip={skip}&$format=json"
        )
        batch = get_json(session, url).get("value") or []
        rows.extend([row for row in batch if isinstance(row, dict)])
        if len(batch) < PAGE_SIZE:
            break
        skip += len(batch)
    return rows


def _try_fetch_plan_rows(
    session: requests.Session,
    base: str,
    entities: list[str],
    ref_key: str,
) -> tuple[str, list[dict[str, Any]], list[str]]:
    errors: list[str] = []
    for entity in entities:
        try:
            rows = _fetch_plan_rows(session, base, entity, ref_key)
        except RuntimeError as exc:
            errors.append(f"{entity}: {exc}")
            continue
        if rows:
            return entity, rows, errors
    return "", [], errors


def _batch_lookup_nomenclature(
    session: requests.Session,
    base: str,
    keys: list[str],
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    unique = [key for key in dict.fromkeys(keys) if key and key != EMPTY_GUID]
    for i in range(0, len(unique), LOOKUP_BATCH):
        chunk = unique[i : i + LOOKUP_BATCH]
        filters = " or ".join(f"Ref_Key eq guid'{key}'" for key in chunk)
        url = (
            f"{base}/Catalog_Номенклатура"
            f"?$filter={quote(filters, safe='')}"
            f"&$select=Ref_Key,Code,Description&$format=json"
        )
        for row in get_json(session, url).get("value") or []:
            ref_key = str(row.get("Ref_Key") or "")
            if ref_key:
                result[ref_key] = row
    return result


def _first_existing(row: dict[str, Any], names: tuple[str, ...]) -> Any:
    for name in names:
        if name in row and row.get(name) not in (None, ""):
            return row.get(name)
    return ""


def _to_display(value: Any) -> str:
    if isinstance(value, bool):
        return "Да" if value else "Нет"
    if value is None:
        return ""
    text = str(value)
    if text.startswith("0001-01-01"):
        return ""
    return text


def _normalize_plan_row(row: dict[str, Any], nomenclature: dict[str, dict[str, Any]]) -> dict[str, Any]:
    nom_key = str(
        _first_existing(
            row,
            (
                "Номенклатура_Key",
                "Продукция_Key",
                "Изделие_Key",
                "Материал_Key",
            ),
        )
        or ""
    )
    nom = nomenclature.get(nom_key) or {}
    return {
        "line": _first_existing(row, ("LineNumber", "НомерСтроки")),
        "date": _format_odata_datetime(
            _first_existing(row, ("ДатаПотребности", "ДатаВыпуска", "Период", "Дата"))
        ),
        "code": str(nom.get("Code") or ""),
        "name": str(nom.get("Description") or "").strip() or nom_key,
        "quantity": _first_existing(row, ("Количество", "КоличествоУпаковок", "План", "КоличествоПлан")),
        "unit": _first_existing(row, ("ЕдиницаИзмерения", "ЕдиницаИзмерения_Key", "Упаковка_Key")),
        "department": _first_existing(
            row,
            ("Подразделение_Key", "Подразделение", "ПодразделениеДиспетчер_Key"),
        ),
        "raw": {
            key: _to_display(value)
            for key, value in row.items()
            if not key.startswith("Ref_") and not key.startswith("DataVersion")
        },
    }


def fetch_latest_production_plan_from_onec() -> dict[str, Any]:
    """Возвращает последний проведенный план производства и строки его табличной части."""
    base = get_odata_base_url().rstrip("/")
    session = create_session()
    try:
        entity_sets: set[str] = set()
        metadata_error = "metadata skipped: using known ERP entity names"

        header = _fetch_latest_approved_header(session, base)
        if header is None:
            return {
                "ok": False,
                "message": "Проведённые планы производства не найдены.",
                "base": base,
                "source": DOCUMENT_ENTITY,
                "count": 0,
                "header": None,
                "items": [],
                "values": [],
                "table_entities": _candidate_table_entities(entity_sets),
                "metadata_error": metadata_error,
            }

        ref_key = str(header.get("Ref_Key") or "")
        table_entities = _candidate_table_entities(entity_sets)
        used_entity, rows, table_errors = _try_fetch_plan_rows(session, base, table_entities, ref_key)

        nom_keys = [
            str(
                _first_existing(
                    row,
                    ("Номенклатура_Key", "Продукция_Key", "Изделие_Key", "Материал_Key"),
                )
                or ""
            )
            for row in rows
        ]
        nomenclature = _batch_lookup_nomenclature(session, base, nom_keys)
        items = [_normalize_plan_row(row, nomenclature) for row in rows]
        values = [
            ["Строка", "Дата", "Код", "Номенклатура", "Количество", "Ед.", "Подразделение"],
            *[
                [
                    _to_display(item["line"]),
                    _to_display(item["date"]),
                    _to_display(item["code"]),
                    _to_display(item["name"]),
                    _to_display(item["quantity"]),
                    _to_display(item["unit"]),
                    _to_display(item["department"]),
                ]
                for item in items
            ],
        ]
        number = str(header.get("Number") or "").strip()
        header_date = _format_odata_datetime(header.get("Date"))
        return {
            "ok": True,
            "message": f"Найден актуальный проведённый план производства №{number} от {header_date}",
            "base": base,
            "source": used_entity or DOCUMENT_ENTITY,
            "count": len(items),
            "header": {
                "ref_key": ref_key,
                "number": number,
                "date": header_date,
                "posted": bool(header.get("Posted")),
                "deletion_mark": bool(header.get("DeletionMark")),
            },
            "items": items,
            "values": values,
            "table_entities": table_entities,
            "metadata_error": metadata_error,
            "table_errors": table_errors[:5],
        }
    except (requests.RequestException, RuntimeError, ValueError, TypeError, ET.ParseError) as exc:
        detail = format_onec_request_error(exc, base_url=base)
        return {
            "ok": False,
            "message": f"Не удалось получить план производства из 1С: {detail}",
            "base": base,
            "source": DOCUMENT_ENTITY,
            "count": 0,
            "header": None,
            "items": [],
            "values": [],
            "table_entities": [],
        }
    finally:
        session.close()
