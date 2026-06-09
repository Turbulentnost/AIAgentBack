"""
Последние служебные записки «Организация совещаний (регл.)» из 1С:ERP.

Алгоритм:
  1. Документы Document_ТД_СлужебнаяЗаписка
  2. Реквизит ТемаСлужебнойЗаписки = ONEC_MEETING_MEMO_THEME из .env
  3. Сортировка по дате (новые первые), берём последние N (по умолчанию 5)
  4. Для каждого документа — memo (ключевые поля СЗ), participants, header, табличные части

Использование:
  python -m app.tools.onec.get_meetings
  python -m app.tools.onec.get_meetings --limit 5 -o memos.json
  python -m app.tools.onec.get_meetings --compact -o memos.json   # без полной шапки header
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any
from urllib.parse import quote
from xml.etree import ElementTree as ET

import requests

from app.core.config import settings
from app.tools.onec.connection import CONFIG, ODataConfig, create_session
from app.tools.onec.meeting_participants import build_combined_document, collect_participants_for_memo

DOCUMENT_ENTITY = "Document_ТД_СлужебнаяЗаписка"
NS = {"edmx": "http://schemas.microsoft.com/ado/2007/06/edmx", "edm": "http://schemas.microsoft.com/ado/2009/11/edm"}


def meeting_theme() -> str:
    return settings.ONEC_MEETING_MEMO_THEME.strip()


def entity_url(base: str, entity: str) -> str:
    return f"{base.rstrip('/')}/{quote(entity)}"


def odata_get_json(
    session: requests.Session,
    url: str,
    *,
    timeout: int = 120,
) -> dict[str, Any]:
    response = session.get(url, timeout=timeout)
    if not response.ok:
        raise RuntimeError(f"HTTP {response.status_code}: {response.text[:800]}")
    return response.json()


def load_metadata_xml(session: requests.Session, config: ODataConfig) -> ET.Element:
    url = f"{config.url.rstrip('/')}/$metadata"
    response = session.get(url, timeout=config.timeout)
    if not response.ok:
        raise RuntimeError(f"HTTP {response.status_code} при загрузке $metadata")
    return ET.fromstring(response.content)


def tabular_entities_from_metadata(root: ET.Element, document_entity: str) -> list[str]:
    prefix = f"{document_entity}_"
    names: list[str] = []
    for entity_type in root.findall(".//edm:EntityType", NS):
        name = entity_type.get("Name") or ""
        if name.startswith(prefix):
            names.append(name)
    return sorted(names)


def theme_catalog_candidates(root: ET.Element) -> list[str]:
    """Ищет тип свойства ТемаСлужебнойЗаписки в метаданных документа."""
    for entity_type in root.findall(".//edm:EntityType", NS):
        if entity_type.get("Name") != DOCUMENT_ENTITY:
            continue
        for prop in entity_type.findall("edm:Property", NS):
            if prop.get("Name") == "ТемаСлужебнойЗаписки":
                type_name = (prop.get("Type") or "").replace("Edm.", "")
                if type_name.startswith("Catalog_"):
                    return [type_name]
    return [
        "Catalog_ТД_ТемыСлужебныхЗаписок",
        "Catalog_ТемыСлужебныхЗаписок",
    ]


def lookup_catalog_key_by_description(
    session: requests.Session,
    config: ODataConfig,
    catalog_entity: str,
    description: str,
) -> str | None:
    safe_desc = description.replace("'", "''")
    filter_expr = f"Description eq '{safe_desc}'"
    url = (
        f"{entity_url(config.url, catalog_entity)}"
        f"?$filter={quote(filter_expr, safe='')}"
        f"&$select={quote('Ref_Key,Description', safe=',_')}"
        f"&$top=1&$format=json"
    )
    try:
        data = odata_get_json(session, url, timeout=config.timeout)
    except RuntimeError:
        return None
    rows = data.get("value") or []
    if rows:
        return rows[0].get("Ref_Key")
    return None


def resolve_theme_key(
    session: requests.Session,
    config: ODataConfig,
    metadata: ET.Element,
) -> str | None:
    theme = meeting_theme()
    for catalog in theme_catalog_candidates(metadata):
        key = lookup_catalog_key_by_description(session, config, catalog, theme)
        if key:
            return key
    return None


def build_filter_candidates(theme_key: str | None) -> list[str]:
    theme = meeting_theme()
    safe_theme = theme.replace("'", "''")
    filters = [
        f"ТемаСлужебнойЗаписки/Description eq '{safe_theme}'",
        f"ТемаСлужебнойЗаписки eq '{safe_theme}'",
        f"contains(ТемаСлужебнойЗаписки, '{safe_theme}')",
    ]
    if theme_key:
        filters.insert(0, f"ТемаСлужебнойЗаписки_Key eq guid'{theme_key}'")
    posted = "Posted eq true and DeletionMark eq false"
    return [f"({item}) and {posted}" for item in filters]


def fetch_documents_by_filter(
    session: requests.Session,
    config: ODataConfig,
    odata_filter: str,
    *,
    limit: int,
    fetch_pool: int,
) -> list[dict[str, Any]]:
    order = quote("Date desc, Number desc", safe=", ")
    url = (
        f"{entity_url(config.url, DOCUMENT_ENTITY)}"
        f"?$filter={quote(odata_filter, safe='')}"
        f"&$orderby={order}"
        f"&$top={fetch_pool}&$format=json"
    )
    data = odata_get_json(session, url, timeout=config.timeout)
    return data.get("value") or []


def theme_matches(row: dict[str, Any], theme_key: str | None) -> bool:
    theme = meeting_theme()
    if theme_key and row.get("ТемаСлужебнойЗаписки_Key") == theme_key:
        return True
    theme_value = row.get("ТемаСлужебнойЗаписки")
    if isinstance(theme_value, str) and theme_value.strip() == theme:
        return True
    if isinstance(theme_value, dict):
        desc = (theme_value.get("Description") or "").strip()
        if desc == theme:
            return True
    for key, value in row.items():
        if not key.startswith("ТемаСлужебнойЗаписки"):
            continue
        if isinstance(value, str) and theme in value:
            return True
    return False


def fetch_recent_documents(
    session: requests.Session,
    config: ODataConfig,
    *,
    limit: int,
    fetch_pool: int,
) -> list[dict[str, Any]]:
    order = quote("Date desc, Number desc", safe=", ")
    url = (
        f"{entity_url(config.url, DOCUMENT_ENTITY)}"
        f"?$filter={quote('DeletionMark eq false', safe='')}"
        f"&$orderby={order}"
        f"&$top={fetch_pool}&$format=json"
    )
    data = odata_get_json(session, url, timeout=config.timeout)
    return data.get("value") or []


def query_last_documents(
    session: requests.Session,
    config: ODataConfig,
    metadata: ET.Element,
    *,
    limit: int,
    fetch_pool: int,
) -> tuple[list[dict[str, Any]], str]:
    theme_key = resolve_theme_key(session, config, metadata)

    for odata_filter in build_filter_candidates(theme_key):
        try:
            rows = fetch_documents_by_filter(
                session, config, odata_filter, limit=limit, fetch_pool=fetch_pool
            )
        except RuntimeError:
            continue
        if rows:
            return rows[:limit], f"OData $filter: {odata_filter}"

    rows = fetch_recent_documents(session, config, limit=limit, fetch_pool=fetch_pool)
    matched = [row for row in rows if theme_matches(row, theme_key)]
    if matched:
        return matched[:limit], "локальный отбор по последним документам (fallback)"

    raise RuntimeError(
        f"Не найдено документов «{DOCUMENT_ENTITY}» с темой «{meeting_theme()}». "
        "Проверьте публикацию OData и имя реквизита ТемаСлужебнойЗаписки."
    )


def fetch_document_header(
    session: requests.Session,
    config: ODataConfig,
    ref_key: str,
) -> dict[str, Any]:
    url = f"{entity_url(config.url, DOCUMENT_ENTITY)}(guid'{ref_key}')?$format=json"
    return odata_get_json(session, url, timeout=config.timeout)


def fetch_tabular_rows(
    session: requests.Session,
    config: ODataConfig,
    tabular_entity: str,
    document_key: str,
) -> list[dict[str, Any]]:
    filter_expr = f"Ref_Key eq guid'{document_key}'"
    url = (
        f"{entity_url(config.url, tabular_entity)}"
        f"?$filter={quote(filter_expr, safe='')}"
        f"&$format=json"
    )
    try:
        data = odata_get_json(session, url, timeout=config.timeout)
    except RuntimeError:
        return []
    return data.get("value") or []


def enrich_document(
    session: requests.Session,
    config: ODataConfig,
    row: dict[str, Any],
    tabular_entities: list[str],
    *,
    include_full_header: bool = True,
) -> dict[str, Any]:
    ref_key = row.get("Ref_Key")
    header = dict(row)
    if ref_key:
        try:
            header = fetch_document_header(session, config, ref_key)
        except RuntimeError:
            header = dict(row)

    tabular_parts: dict[str, list[dict[str, Any]]] = {}
    if ref_key:
        for entity in tabular_entities:
            section_name = entity[len(DOCUMENT_ENTITY) + 1 :]
            rows = fetch_tabular_rows(session, config, entity, ref_key)
            if rows:
                tabular_parts[section_name] = rows

    participants = collect_participants_for_memo(
        header,
        session=session,
        config=config,
    )

    return build_combined_document(
        header,
        participants,
        tabular_sections=tabular_parts,
        include_full_header=include_full_header,
    )


def get_last_meeting_memos(
    *,
    limit: int = 5,
    fetch_pool: int = 200,
    config: ODataConfig = CONFIG,
    include_full_header: bool = True,
) -> dict[str, Any]:
    session = create_session(config)
    metadata = load_metadata_xml(session, config)
    tabular_entities = tabular_entities_from_metadata(metadata, DOCUMENT_ENTITY)

    documents, method = query_last_documents(
        session,
        config,
        metadata,
        limit=limit,
        fetch_pool=fetch_pool,
    )

    result_documents = [
        enrich_document(
            session,
            config,
            row,
            tabular_entities,
            include_full_header=include_full_header,
        )
        for row in documents
    ]

    theme = meeting_theme()
    return {
        "document_type": DOCUMENT_ENTITY,
        "theme": theme,
        "limit": limit,
        "count": len(result_documents),
        "selection_method": method,
        "tabular_entities": tabular_entities,
        "documents": result_documents,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Последние служебные записки ERP с темой "
            f"«{settings.ONEC_MEETING_MEMO_THEME}» и все данные по ним (OData)."
        )
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=5,
        metavar="N",
        help="Сколько последних документов вернуть (по умолчанию 5)",
    )
    parser.add_argument(
        "--fetch-pool",
        type=int,
        default=200,
        metavar="N",
        help="Сколько документов запрашивать у сервера для отбора/fallback",
    )
    parser.add_argument(
        "--compact",
        action="store_true",
        help="Не включать полную шапку header (только memo + participants)",
    )
    parser.add_argument(
        "-o",
        "--output",
        help="Путь к JSON-файлу (иначе вывод в stdout)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    args = build_parser().parse_args(argv)
    if args.limit < 1:
        print("Ошибка: --limit должен быть >= 1", file=sys.stderr)
        return 1

    try:
        payload = get_last_meeting_memos(
            limit=args.limit,
            fetch_pool=max(args.fetch_pool, args.limit),
            include_full_header=not args.compact,
        )
    except requests.RequestException as error:
        print(f"Ошибка сети: {error}", file=sys.stderr)
        return 1
    except RuntimeError as error:
        print(f"Ошибка: {error}", file=sys.stderr)
        return 1

    text = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as file:
            file.write(text)
        print(f"Сохранено: {args.output} ({payload['count']} документов)")
    else:
        print(text)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
