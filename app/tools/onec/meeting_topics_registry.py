"""
Реестр тем совещаний 1С:ERP — справочник Catalog_ТД_ТемыСовещаний (OData).

Примеры:
  python -m app.tools.onec.meeting_topics_registry
  python -m app.tools.onec.meeting_topics_registry --query Turbo --limit 10
  python -m app.tools.onec.meeting_topics_registry --meeting-type Отчетное --active-only
  python -m app.tools.onec.meeting_topics_registry --ref-key c65ad1af-71d1-11ef-9573-6cb31113810c
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from typing import Any
from urllib.parse import quote

import requests

from app.tools.onec.connection import CONFIG, ODataConfig, create_session
from app.tools.onec.get_meetings import entity_url, odata_get_json

CATALOG_ENTITY = "Catalog_ТД_ТемыСовещаний"
EMPTY_DATE = "0001-01-01T00:00:00"
EXPAND_FIELDS = "Руководитель,Проверяющий,Подразделение,Кабинет,Проект,Комитет"
EMPTY_GUID = "00000000-0000-0000-0000-000000000000"


def odata_escape(value: str) -> str:
    return value.replace("'", "''")


def is_empty_key(value: str | None) -> bool:
    normalized = (value or "").strip()
    return not normalized or normalized == EMPTY_GUID


def normalize_key(value: str | None) -> str | None:
    return None if is_empty_key(value) else value


def normalize_optional_datetime(value: str | None) -> str | None:
    return None if is_empty_date(value) else value


def is_empty_date(value: str | None) -> bool:
    normalized = (value or "").strip()
    return not normalized or normalized.startswith(EMPTY_DATE)


def parse_closed_date(value: str | None) -> date | None:
    if is_empty_date(value):
        return None
    normalized = (value or "").strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(normalized).date()
    except ValueError:
        return None


def is_topic_active(raw_closed_date: str | None, *, today: date | None = None) -> bool:
    """Тема активна, если дата закрытия пустая или ещё не наступила."""
    closed = parse_closed_date(raw_closed_date)
    if closed is None:
        return True
    current = today or date.today()
    return closed >= current


def related_description(value: Any) -> str | None:
    if isinstance(value, dict):
        description = (value.get("Description") or "").strip()
        return description or None
    return None


def normalize_topic(row: dict[str, Any], *, expand_related: bool) -> dict[str, Any]:
    topic: dict[str, Any] = {
        "ref_key": row.get("Ref_Key"),
        "code": row.get("Code"),
        "description": (row.get("Description") or "").strip(),
        "meeting_type": row.get("ВидСовещания"),
        "priority": row.get("Приоритет"),
        "schedule_defined": bool(row.get("РасписаниеЗадано")),
        "start_time": normalize_optional_datetime(row.get("ВремяНачалаСовещания")),
        "end_time": normalize_optional_datetime(row.get("ВремяОкончанияСовещания")),
        "start_date": normalize_optional_datetime(row.get("ДатаНачала")),
        "end_date": normalize_optional_datetime(row.get("ДатаКонца")),
        "closed_date": normalize_optional_datetime(row.get("ДатаЗакрытияТемы")),
        "is_active": is_topic_active(row.get("ДатаЗакрытияТемы")),
        "is_project_topic": bool(row.get("ПоПроекту")),
        "is_management_circle_topic": bool(row.get("ТемаКругаУправления")),
        "repeat": {
            "days": row.get("ПериодПовтораДней"),
            "weeks": row.get("ПериодНедель"),
            "months": row.get("ПериодМесяцев"),
            "years": row.get("ПериодЛет"),
            "count": row.get("КоличествоПовторов"),
            "weekdays": row.get("ПовторениеПоДнямНедели") or [],
            "months_of_year": row.get("ПовторениеПоМесяцам") or [],
        },
        "keys": {
            "project": normalize_key(row.get("Проект_Key")),
            "manager": normalize_key(row.get("Руководитель_Key")),
            "reviewer": normalize_key(row.get("Проверяющий_Key")),
            "department": normalize_key(row.get("Подразделение_Key")),
            "room": normalize_key(row.get("Кабинет_Key")),
            "committee": normalize_key(row.get("Комитет_Key")),
            "organization": normalize_key(row.get("Организация_Key")),
            "basis": normalize_key(row.get("Основание_Key")),
        },
    }

    if expand_related:
        topic["manager"] = related_description(row.get("Руководитель"))
        topic["reviewer"] = related_description(row.get("Проверяющий"))
        topic["department"] = related_description(row.get("Подразделение"))
        topic["room"] = related_description(row.get("Кабинет"))
        topic["project"] = related_description(row.get("Проект"))
        topic["committee"] = related_description(row.get("Комитет"))

    return topic


def build_filter_parts(
    *,
    query: str | None,
    code: str | None,
    meeting_type: str | None,
    active_only: bool,
    ref_key: str | None,
) -> list[str]:
    parts = ["DeletionMark eq false"]

    if ref_key:
        parts.append(f"Ref_Key eq guid'{ref_key}'")
        return parts

    if code:
        parts.append(f"Code eq '{odata_escape(code.strip())}'")

    if meeting_type:
        parts.append(f"ВидСовещания eq '{odata_escape(meeting_type.strip())}'")

    if query:
        parts.append(f"substringof('{odata_escape(query.strip())}', Description)")

    if active_only:
        today = date.today().isoformat()
        parts.append(
            f"(ДатаЗакрытияТемы eq datetime'{EMPTY_DATE}' "
            f"or ДатаЗакрытияТемы ge datetime'{today}T00:00:00')"
        )

    return parts


def build_list_url(
    config: ODataConfig,
    *,
    odata_filter: str,
    limit: int,
    expand_related: bool,
) -> str:
    order = quote("Description", safe="")
    url = (
        f"{entity_url(config.url, CATALOG_ENTITY)}"
        f"?$filter={quote(odata_filter, safe='')}"
        f"&$orderby={order}"
        f"&$top={limit}&$format=json"
    )
    if expand_related:
        url += f"&$expand={quote(EXPAND_FIELDS, safe=',')}"
    return url


def fetch_topic_by_key(
    session: requests.Session,
    config: ODataConfig,
    ref_key: str,
    *,
    expand_related: bool,
) -> dict[str, Any] | None:
    url = f"{entity_url(config.url, CATALOG_ENTITY)}(guid'{ref_key}')?$format=json"
    if expand_related:
        url += f"&$expand={quote(EXPAND_FIELDS, safe=',')}"
    try:
        row = odata_get_json(session, url, timeout=config.timeout)
    except RuntimeError:
        return None
    if row.get("DeletionMark"):
        return None
    return row


def query_meeting_topics(
    *,
    query: str | None = None,
    code: str | None = None,
    meeting_type: str | None = None,
    active_only: bool = True,
    ref_key: str | None = None,
    limit: int = 20,
    expand_related: bool = True,
    config: ODataConfig = CONFIG,
) -> dict[str, Any]:
    if limit < 1:
        raise ValueError("limit must be >= 1")

    session = create_session(config)
    filters = build_filter_parts(
        query=query,
        code=code,
        meeting_type=meeting_type,
        active_only=active_only,
        ref_key=ref_key,
    )
    odata_filter = " and ".join(filters)

    if ref_key:
        row = fetch_topic_by_key(session, config, ref_key, expand_related=expand_related)
        rows = [row] if row else []
        method = f"OData by Ref_Key: {ref_key}"
    else:
        url = build_list_url(
            config,
            odata_filter=odata_filter,
            limit=limit,
            expand_related=expand_related,
        )
        data = odata_get_json(session, url, timeout=config.timeout)
        rows = data.get("value") or []
        method = f"OData $filter: {odata_filter}"

    topics = [normalize_topic(row, expand_related=expand_related) for row in rows]
    return {
        "catalog_entity": CATALOG_ENTITY,
        "count": len(topics),
        "limit": limit,
        "filters": {
            "query": query,
            "code": code,
            "meeting_type": meeting_type,
            "active_only": active_only,
            "ref_key": ref_key,
        },
        "selection_method": method,
        "topics": topics,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Реестр тем совещаний 1С:ERP (Catalog_ТД_ТемыСовещаний) через OData."
    )
    parser.add_argument("--query", help="Поиск по наименованию темы (substringof)")
    parser.add_argument("--code", help="Точный код элемента справочника")
    parser.add_argument("--meeting-type", help="Вид совещания, например «Отчетное»")
    parser.add_argument(
        "--ref-key",
        help="GUID элемента справочника (вернуть одну тему)",
    )
    parser.add_argument(
        "--include-closed",
        action="store_true",
        help="Включать закрытые темы (по умолчанию только активные)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        metavar="N",
        help="Максимум записей (по умолчанию 20)",
    )
    parser.add_argument(
        "--compact",
        action="store_true",
        help="Не разворачивать связанные объекты (руководитель, подразделение и т.д.)",
    )
    parser.add_argument("-o", "--output", help="Путь к JSON-файлу (иначе stdout)")
    return parser


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    args = build_parser().parse_args(argv)
    if args.limit < 1:
        print("Ошибка: --limit должен быть >= 1", file=sys.stderr)
        return 1

    try:
        payload = query_meeting_topics(
            query=args.query,
            code=args.code,
            meeting_type=args.meeting_type,
            active_only=not args.include_closed,
            ref_key=args.ref_key,
            limit=args.limit,
            expand_related=not args.compact,
        )
    except requests.RequestException as error:
        print(f"Ошибка сети: {error}", file=sys.stderr)
        return 1
    except (RuntimeError, ValueError) as error:
        print(f"Ошибка: {error}", file=sys.stderr)
        return 1

    text = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as file:
            file.write(text)
        print(f"Сохранено: {args.output} ({payload['count']} тем)")
    else:
        print(text)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
