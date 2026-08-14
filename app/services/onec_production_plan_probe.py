"""TEMP/Aveon: чтение актуального утвержденного плана производства из 1C OData."""

from __future__ import annotations

from datetime import date, datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
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
PRODUCTION_PLAN_REGISTER_ENTITY = "AccumulationRegister_ПланыПроизводства"
EMPTY_GUID = "00000000-0000-0000-0000-000000000000"
PAGE_SIZE = 200
REGISTER_PAGE_SIZE = 20
LOOKUP_BATCH = 40
HEADER_SCAN_LIMIT = 500
FALLBACK_TABLE_ENTITIES = [
    "Document_ПланПроизводства_Продукция",
    "Document_ПланПроизводства_ПродукцияПлан",
    "Document_ПланПроизводства_ВыпускПродукции",
    "Document_ПланПроизводства_План",
    "Document_ПланПроизводства_Материалы",
    "Document_ПланПроизводства_Товары",
]
HEADER_SELECT_MINIMAL = "Ref_Key,Number,Date,Posted,DeletionMark"
HEADER_SELECT_WITH_PERIOD = f"{HEADER_SELECT_MINIMAL},НачалоПериода,ОкончаниеПериода"
HEADER_BUSINESS_KEY_FIELDS = (
    "Сценарий_Key",
    "ВидПлана_Key",
    "ПодразделениеДиспетчер_Key",
    "Подразделение_Key",
    "Ответственный_Key",
)
HEADER_BUSINESS_NAME_FIELDS = (
    "Сценарий",
    "ВидПлана",
    "ПодразделениеДиспетчер",
    "Подразделение",
)
HEADER_SELECT_BUSINESS_KEYS = f"{HEADER_SELECT_WITH_PERIOD},{','.join(HEADER_BUSINESS_KEY_FIELDS)}"
HEADER_SELECT_BUSINESS = (
    f"{HEADER_SELECT_BUSINESS_KEYS},{','.join(HEADER_BUSINESS_NAME_FIELDS)}"
)


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
    if metadata_candidates:
        return metadata_candidates
    return list(dict.fromkeys([*metadata_candidates, *FALLBACK_TABLE_ENTITIES]))


def _available_table_entities(
    session: requests.Session,
    base: str,
    entities: list[str],
) -> tuple[list[str], list[str]]:
    """Отсекает несуществующие fallback-табличные части одним быстрым пробным запросом."""
    available: list[str] = []
    errors: list[str] = []
    for entity in entities:
        url = f"{base}/{entity}?$top=1&$format=json"
        try:
            get_json(session, url)
        except RuntimeError as exc:
            errors.append(f"{entity}: {exc}")
            continue
        available.append(entity)
    return available, errors


def _fetch_register_rows_by_plan_ref(
    session: requests.Session,
    base: str,
    plan_ref_keys: set[str],
) -> dict[str, list[dict[str, Any]]]:
    """Читает регистр планов производства и группирует движения по Ref_Key документа плана."""
    refs = [ref.strip() for ref in plan_ref_keys if ref.strip()]
    if not refs:
        return {}

    result: dict[str, list[dict[str, Any]]] = {}
    for ref in refs:
        raw_filter = f"RecordSet/any(d:d/ПланПроизводства_Key eq guid'{ref}')"
        encoded_filter = quote(raw_filter, safe="'():/ ")
        url = (
            f"{base}/{PRODUCTION_PLAN_REGISTER_ENTITY}?"
            f"$top={REGISTER_PAGE_SIZE}&$filter={encoded_filter}&$format=json"
        )
        try:
            records = get_json(session, url).get("value") or []
        except RuntimeError:
            continue
        for record in records:
            if not isinstance(record, dict):
                continue
            for row in record.get("RecordSet") or []:
                if not isinstance(row, dict):
                    continue
                plan_ref = str(row.get("ПланПроизводства_Key") or "").strip()
                if plan_ref.lower() != ref.lower():
                    continue
                if row.get("Active") is False:
                    continue
                status = str(row.get("Статус") or "").casefold().replace("ё", "е")
                if status and "отмен" in status:
                    continue
                result.setdefault(plan_ref, []).append(row)
    return result


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
    for key in ("Статус", "Состояние", "СтатусДокумента"):
        value = str(row.get(key) or "").casefold().replace("ё", "е")
        if value and "отмен" in value:
            return False
    return True


def _header_period_bounds(row: dict[str, Any]) -> tuple[date | None, date | None]:
    period_start = _parse_odata_datetime(
        row.get("НачалоПериода") or row.get("НачалоПериодаПланирования")
    )
    period_end = _parse_odata_datetime(row.get("ОкончаниеПериода"))
    return (
        period_start.date() if period_start else None,
        period_end.date() if period_end else None,
    )


def _header_overlaps_year(row: dict[str, Any], year: int) -> bool:
    year_start = date(year, 1, 1)
    year_end = date(year, 12, 31)
    period_start, period_end = _header_period_bounds(row)
    if period_start is not None and period_end is not None:
        return period_start <= year_end and period_end >= year_start
    doc_date = _parse_odata_datetime(row.get("Date"))
    return doc_date is not None and doc_date.year == year


def _fetch_header_page(
    session: requests.Session,
    base: str,
    *,
    skip: int,
    select: str,
) -> list[dict[str, Any]]:
    url = (
        f"{base}/{DOCUMENT_ENTITY}"
        f"?$orderby={quote('Date desc', safe='')}"
        f"&$select={quote(select, safe=',')}"
        f"&$top=50&$skip={skip}&$format=json"
    )
    return get_json(session, url).get("value") or []


def _fetch_approved_headers(session: requests.Session, base: str, *, year: int) -> list[dict[str, Any]]:
    """Проведённые планы производства, пересекающие указанный год."""
    skip = 0
    approved: list[dict[str, Any]] = []
    seen_refs: set[str] = set()
    header_select = HEADER_SELECT_BUSINESS
    while skip < HEADER_SCAN_LIMIT:
        try:
            rows = _fetch_header_page(session, base, skip=skip, select=header_select)
        except RuntimeError as exc:
            if header_select == HEADER_SELECT_BUSINESS:
                header_select = HEADER_SELECT_BUSINESS_KEYS
                skip = 0
                approved.clear()
                seen_refs.clear()
                continue
            if header_select == HEADER_SELECT_BUSINESS_KEYS:
                header_select = HEADER_SELECT_WITH_PERIOD
                skip = 0
                approved.clear()
                seen_refs.clear()
                continue
            if header_select == HEADER_SELECT_WITH_PERIOD:
                header_select = HEADER_SELECT_MINIMAL
                skip = 0
                approved.clear()
                seen_refs.clear()
                continue
            raise exc
        if not rows:
            break
        for row in rows:
            if not isinstance(row, dict) or not _is_approved_header(row):
                continue
            ref_key = str(row.get("Ref_Key") or "")
            if not ref_key or ref_key in seen_refs:
                continue
            if not _header_overlaps_year(row, year):
                continue
            seen_refs.add(ref_key)
            approved.append(row)
        if len(rows) < 50:
            break
        skip += 50
    approved.sort(
        key=lambda row: _parse_odata_datetime(row.get("Date")) or datetime.min,
        reverse=True,
    )
    return approved


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


def _fetch_all_plan_rows(
    session: requests.Session,
    base: str,
    entities: list[str],
    ref_key: str,
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    """Читает строки из всех доступных табличных частей документа."""
    errors: list[str] = []
    used_entities: list[str] = []
    merged: list[dict[str, Any]] = []
    seen_line_refs: set[str] = set()

    def _fetch_entity(entity: str) -> tuple[str, list[dict[str, Any]], str | None]:
        local_session = create_session()
        try:
            rows = _fetch_plan_rows(local_session, base, entity, ref_key)
        except RuntimeError as exc:
            return entity, [], f"{entity}: {exc}"
        finally:
            local_session.close()
        return entity, rows, None

    if not entities:
        return merged, used_entities, errors

    # Табличные части независимы: проверяем все, но параллельно, чтобы не ждать
    # несколько последовательных OData round-trip на каждый документ.
    with ThreadPoolExecutor(max_workers=min(4, len(entities))) as pool:
        futures = [pool.submit(_fetch_entity, entity) for entity in entities]
        entity_results = [future.result() for future in as_completed(futures)]

    order = {entity: index for index, entity in enumerate(entities)}
    entity_results.sort(key=lambda item: order.get(item[0], 999))

    for entity, rows, error in entity_results:
        if error:
            errors.append(error)
            continue
        if rows:
            used_entities.append(entity)
        for row in rows:
            line_ref = str(row.get("Ref_Key") or "")
            if line_ref:
                if line_ref in seen_line_refs:
                    continue
                seen_line_refs.add(line_ref)
            merged.append(row)
    return merged, used_entities, errors


def _try_fetch_plan_rows(
    session: requests.Session,
    base: str,
    entities: list[str],
    ref_key: str,
) -> tuple[str, list[dict[str, Any]], list[str]]:
    rows, used_entities, errors = _fetch_all_plan_rows(session, base, entities, ref_key)
    source = used_entities[0] if used_entities else ""
    return source, rows, errors


def _batch_lookup_resource_specs(
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
            f"{base}/Catalog_РесурсныеСпецификации"
            f"?$filter={quote(filters, safe='')}"
            f"&$select=Ref_Key,Code,Description&$format=json"
        )
        for row in get_json(session, url).get("value") or []:
            ref_key = str(row.get("Ref_Key") or "")
            if ref_key:
                result[ref_key] = row
    return result


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


def _normalize_plan_row(
    row: dict[str, Any],
    nomenclature: dict[str, dict[str, Any]],
    resource_specs: dict[str, dict[str, Any]],
) -> dict[str, Any]:
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
    spec_key = str(
        _first_existing(
            row,
            (
                "Спецификация_Key",
                "РесурснаяСпецификация_Key",
                "СпецификацияПродукции_Key",
            ),
        )
        or ""
    )
    spec = resource_specs.get(spec_key) or {}
    spec_name = str(spec.get("Description") or _to_display(row.get("Спецификация")) or "").strip()
    return {
        "line": _first_existing(row, ("LineNumber", "НомерСтроки")),
        "date": _format_odata_datetime(
            _first_existing(
                row,
                (
                    "Period",
                    "Период",
                    "ДатаПотребности",
                    "ДатаВыпуска",
                    "ДатаВыпускаПродукции",
                    "ДатаПроизводства",
                    "ДатаПланирования",
                    "ПериодПланирования",
                    "ДатаНачала",
                    "ДатаОкончания",
                    "Дата",
                ),
            )
        ),
        "code": str(nom.get("Code") or ""),
        "name": str(nom.get("Description") or "").strip() or nom_key,
        "spec_key": spec_key,
        "spec_name": spec_name,
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


def _normalize_header(row: dict[str, Any]) -> dict[str, Any]:
    ref_key = str(row.get("Ref_Key") or "")
    period_start = _parse_odata_datetime(
        row.get("НачалоПериода") or row.get("НачалоПериодаПланирования")
    )
    period_end = _parse_odata_datetime(row.get("ОкончаниеПериода"))
    return {
        "ref_key": ref_key,
        "number": str(row.get("Number") or "").strip(),
        "date": _format_odata_datetime(row.get("Date")),
        "posted": bool(row.get("Posted")),
        "deletion_mark": bool(row.get("DeletionMark")),
        "period_start": period_start.isoformat() if period_start else "",
        "period_end": period_end.isoformat() if period_end else "",
        "scenario_key": str(row.get("Сценарий_Key") or ""),
        "scenario_name": _to_display(row.get("Сценарий")),
        "plan_type_key": str(row.get("ВидПлана_Key") or ""),
        "plan_type_name": _to_display(row.get("ВидПлана")),
        "dispatcher_department_key": str(
            row.get("ПодразделениеДиспетчер_Key") or row.get("Подразделение_Key") or ""
        ),
        "dispatcher_department_name": _to_display(
            row.get("ПодразделениеДиспетчер") or row.get("Подразделение")
        ),
        "responsible_key": str(row.get("Ответственный_Key") or ""),
    }


def _build_document_payload(
    session: requests.Session,
    base: str,
    header_row: dict[str, Any],
    table_entities: list[str],
) -> dict[str, Any] | None:
    ref_key = str(header_row.get("Ref_Key") or "")
    if not ref_key:
        return None
    rows, used_entities, table_errors = _fetch_all_plan_rows(session, base, table_entities, ref_key)
    if not rows:
        return None

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
    spec_keys = [
        str(
            _first_existing(
                row,
                (
                    "Спецификация_Key",
                    "РесурснаяСпецификация_Key",
                    "СпецификацияПродукции_Key",
                ),
            )
            or ""
        )
        for row in rows
    ]
    resource_specs = _batch_lookup_resource_specs(session, base, spec_keys)
    items = [_normalize_plan_row(row, nomenclature, resource_specs) for row in rows]
    source = ", ".join(used_entities) if used_entities else DOCUMENT_ENTITY
    header = _normalize_header(header_row)
    number = header["number"]
    header_date = header["date"]
    return {
        "header": header,
        "items": items,
        "source": source,
        "count": len(items),
        "message": f"План производства №{number} от {header_date}",
        "table_entities": used_entities,
        "table_errors": table_errors[:5],
    }


def _build_document_rows_payload(
    session: requests.Session,
    base: str,
    header_row: dict[str, Any],
    table_entities: list[str],
) -> dict[str, Any] | None:
    ref_key = str(header_row.get("Ref_Key") or "")
    if not ref_key:
        return None
    rows, used_entities, table_errors = _fetch_all_plan_rows(session, base, table_entities, ref_key)
    if not rows:
        return None
    return {
        "header_row": header_row,
        "rows": rows,
        "used_entities": used_entities,
        "table_errors": table_errors,
    }


def _normalize_document_payload(
    raw_document: dict[str, Any],
    nomenclature: dict[str, dict[str, Any]],
    resource_specs: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    rows = raw_document.get("rows") or []
    used_entities = raw_document.get("used_entities") or []
    table_errors = raw_document.get("table_errors") or []
    header_row = raw_document.get("header_row") or {}
    items = [_normalize_plan_row(row, nomenclature, resource_specs) for row in rows]
    source = ", ".join(used_entities) if used_entities else DOCUMENT_ENTITY
    header = _normalize_header(header_row)
    number = header["number"]
    header_date = header["date"]
    return {
        "header": header,
        "items": items,
        "source": source,
        "count": len(items),
        "message": f"План производства №{number} от {header_date}",
        "table_entities": used_entities,
        "table_errors": table_errors[:5],
    }


def fetch_production_plans_for_year(year: int | None = None) -> dict[str, Any]:
    """Возвращает все проведённые планы производства, актуальные для указанного года."""
    target_year = year or date.today().year
    base = get_odata_base_url().rstrip("/")
    session = create_session()
    try:
        entity_sets: set[str] = set()
        metadata_error = ""
        try:
            entity_sets = _metadata_entity_sets(_fetch_metadata(session, base))
        except (RuntimeError, requests.RequestException, ET.ParseError) as exc:
            metadata_error = f"metadata unavailable: {exc}"
        table_entities = _candidate_table_entities(entity_sets)
        if not entity_sets:
            table_entities, availability_errors = _available_table_entities(
                session,
                base,
                table_entities,
            )
            if availability_errors and not metadata_error:
                metadata_error = "; ".join(availability_errors[:3])
        headers = _fetch_approved_headers(session, base, year=target_year)
        if not headers:
            return {
                "ok": False,
                "year": target_year,
                "message": f"Проведённые планы производства за {target_year} год не найдены.",
                "base": base,
                "source": DOCUMENT_ENTITY,
                "count": 0,
                "documents": [],
                "header": None,
                "items": [],
                "values": [],
                "table_entities": table_entities,
                "metadata_error": metadata_error,
            }

        plan_refs = {str(header.get("Ref_Key") or "") for header in headers}
        register_rows_by_plan = _fetch_register_rows_by_plan_ref(session, base, plan_refs)
        raw_documents: list[dict[str, Any]] = [
            {
                "header_row": header,
                "rows": register_rows,
                "used_entities": [PRODUCTION_PLAN_REGISTER_ENTITY],
                "table_errors": [],
            }
            for header in headers
            if (register_rows := register_rows_by_plan.get(str(header.get("Ref_Key") or "")))
        ]
        headers_without_register_rows = [
            header
            for header in headers
            if str(header.get("Ref_Key") or "") not in register_rows_by_plan
        ]
        if len(headers_without_register_rows) == 1:
            document = _build_document_rows_payload(
                session,
                base,
                headers_without_register_rows[0],
                table_entities,
            )
            if document is not None:
                raw_documents.append(document)
        elif headers_without_register_rows:
            def _build_one(header_row: dict[str, Any]) -> dict[str, Any] | None:
                local_session = create_session()
                try:
                    return _build_document_rows_payload(
                        local_session, base, header_row, table_entities
                    )
                finally:
                    local_session.close()

            workers = min(4, len(headers_without_register_rows))
            with ThreadPoolExecutor(max_workers=workers) as pool:
                for document in pool.map(_build_one, headers_without_register_rows):
                    if document is not None:
                        raw_documents.append(document)

        if not raw_documents:
            return {
                "ok": False,
                "year": target_year,
                "message": f"Документы плана за {target_year} год найдены, но строк табличных частей нет.",
                "base": base,
                "source": DOCUMENT_ENTITY,
                "count": 0,
                "documents": [],
                "header": None,
                "items": [],
                "values": [],
                "table_entities": table_entities,
                "metadata_error": metadata_error,
            }

        all_rows = [row for document in raw_documents for row in document.get("rows", [])]
        nom_keys = [
            str(
                _first_existing(
                    row,
                    ("Номенклатура_Key", "Продукция_Key", "Изделие_Key", "Материал_Key"),
                )
                or ""
            )
            for row in all_rows
        ]
        spec_keys = [
            str(
                _first_existing(
                    row,
                    (
                        "Спецификация_Key",
                        "РесурснаяСпецификация_Key",
                        "СпецификацияПродукции_Key",
                    ),
                )
                or ""
            )
            for row in all_rows
        ]
        nomenclature = _batch_lookup_nomenclature(session, base, nom_keys)
        resource_specs = _batch_lookup_resource_specs(session, base, spec_keys)
        documents = [
            _normalize_document_payload(document, nomenclature, resource_specs)
            for document in raw_documents
        ]

        primary = documents[0]
        total_count = sum(doc["count"] for doc in documents)
        numbers = ", ".join(doc["header"]["number"] for doc in documents[:3])
        if len(documents) > 3:
            numbers = f"{numbers} и ещё {len(documents) - 3}"
        return {
            "ok": True,
            "year": target_year,
            "message": (
                f"Загружено {len(documents)} план(ов) производства за {target_year} год "
                f"({numbers}), строк: {total_count}"
            ),
            "base": base,
            "source": primary["source"],
            "count": total_count,
            "documents": documents,
            "header": primary["header"],
            "items": primary["items"],
            "values": [],
            "table_entities": table_entities,
            "metadata_error": metadata_error,
        }
    except (requests.RequestException, RuntimeError, ValueError, TypeError, ET.ParseError) as exc:
        detail = format_onec_request_error(exc, base_url=base)
        return {
            "ok": False,
            "year": target_year,
            "message": f"Не удалось получить план производства из 1С: {detail}",
            "base": base,
            "source": DOCUMENT_ENTITY,
            "count": 0,
            "documents": [],
            "header": None,
            "items": [],
            "values": [],
            "table_entities": [],
        }
    finally:
        session.close()


def fetch_latest_production_plan_from_onec() -> dict[str, Any]:
    """Обратная совместимость: актуальные планы текущего года."""
    return fetch_production_plans_for_year(date.today().year)
