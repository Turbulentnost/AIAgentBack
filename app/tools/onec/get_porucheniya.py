"""
Поручения из 1С:ERP (OData).

Читает табличную часть Document_ТД_Поручения_Поручения за период по сроку
исполнения и обогащает строки шапкой Document_ТД_Поручения. Приоритет
рассчитывается по правилам отчёта 1С.

CLI:
  python -m app.tools.onec.get_porucheniya
  python -m app.tools.onec.get_porucheniya --start 2026-01-01 --end 2026-06-30
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta
from typing import Any
from urllib.parse import quote

import requests

from app.integrations.onec_odata import fetch_all
from app.tools.onec.connection import CONFIG, ODataConfig, create_session
from app.tools.onec.get_meetings import entity_url, odata_get_json
from app.tools.onec.lookup_user_ref import (
    USER_CATALOG,
    is_empty_key,
    load_persons_for_keys,
)

PORUCHENIYA_TABULAR = "Document_ТД_Поручения_Поручения"
PORUCHENIYA_DOCUMENT = "Document_ТД_Поручения"
EMPTY_DATE = "0001-01-01T00:00:00"
CRITICAL_MANAGER_FIO = "Амураль Игорь Борисович"
THREE_DAYS = timedelta(seconds=259200)
TEN_DAYS = timedelta(seconds=864000)


def parse_input_date(value: str | date | None, *, default: date | None = None) -> date:
    if value is None:
        if default is None:
            raise ValueError("Дата не указана")
        return default
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    normalized = str(value).strip()
    if not normalized:
        if default is None:
            raise ValueError("Дата не указана")
        return default
    return date.fromisoformat(normalized[:10])


def parse_onec_datetime(value: str | None) -> datetime | None:
    if not value or value.startswith(EMPTY_DATE[:10]):
        return None
    normalized = value.strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is not None:
        parsed = parsed.replace(tzinfo=None)
    return parsed


def start_of_day(value: datetime) -> datetime:
    return value.replace(hour=0, minute=0, second=0, microsecond=0)


def end_of_day(value: datetime) -> datetime:
    return value.replace(hour=23, minute=59, second=59, microsecond=0)


def format_odata_datetime(day: date, *, end: bool = False) -> str:
    if end:
        return f"datetime'{day.isoformat()}T23:59:59'"
    return f"datetime'{day.isoformat()}T00:00:00'"


def person_description(row: dict[str, Any] | None) -> str:
    if not row:
        return ""
    return (row.get("Description") or row.get("ФИО") or "").strip()


def user_description(row: dict[str, Any] | None) -> str:
    if not row:
        return ""
    return (row.get("Description") or "").strip()


def load_users_for_keys(
    session: requests.Session,
    user_keys: set[str],
    *,
    config: ODataConfig = CONFIG,
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
            f"&$select={quote('Ref_Key,Description,DeletionMark', safe=',_')}"
            f"&$format=json"
        )
        for row in fetch_all(session, url, page=100, timeout=config.timeout):
            if row.get("Ref_Key") and not row.get("DeletionMark"):
                result[row["Ref_Key"]] = row
    return result


def load_documents_for_keys(
    session: requests.Session,
    document_keys: set[str],
    *,
    entity: str,
    config: ODataConfig = CONFIG,
) -> dict[str, dict[str, Any]]:
    keys = [key for key in document_keys if not is_empty_key(key)]
    if not keys:
        return {}

    result: dict[str, dict[str, Any]] = {}
    chunk_size = 10
    for offset in range(0, len(keys), chunk_size):
        chunk = keys[offset : offset + chunk_size]
        filter_expr = " or ".join(f"Ref_Key eq guid'{key}'" for key in chunk)
        url = (
            f"{entity_url(config.url, entity)}"
            f"?$filter={quote(filter_expr, safe='')}"
            f"&$format=json"
        )
        for row in fetch_all(session, url, page=50, timeout=config.timeout):
            if row.get("Ref_Key"):
                result[row["Ref_Key"]] = row
    return result


def compute_priority(
    *,
    due_date: datetime | None,
    confirmed: bool,
    completed: bool,
    has_file: bool,
    manager: str,
    now: datetime,
) -> str:
    if due_date is None:
        priority = "Средний"
    else:
        now_start = start_of_day(now)
        now_end = end_of_day(now)
        due_start = start_of_day(due_date)
        due_end = end_of_day(due_date)
        tomorrow_end = end_of_day(now + timedelta(days=1))

        if (now - THREE_DAYS) >= due_date and not confirmed:
            priority = "Средний"
        elif now_start == due_start or tomorrow_end == due_end:
            priority = "Высокий"
        elif now_end >= due_end:
            priority = "Высокий"
        elif (now_end - TEN_DAYS) >= due_end:
            priority = "Критический"
        elif manager == CRITICAL_MANAGER_FIO:
            priority = "Критический"
        else:
            priority = "Средний"

    if completed and not has_file:
        priority = "Высокий"
    return priority


def normalize_poruchenie_row(
    row: dict[str, Any],
    parent: dict[str, Any],
    *,
    users: dict[str, dict[str, Any]],
    persons: dict[str, dict[str, Any]],
    now: datetime,
) -> dict[str, Any]:
    due_date = parse_onec_datetime(row.get("СрокИсполнения"))
    overdue = due_date is not None and due_date < now
    manager = users.get(parent.get("Руководитель_Key") or "", {})
    secretary = users.get(parent.get("СекретарьРК_Key") or "", {})
    responsible = persons.get(row.get("ОтветственноеЛицо_Key") or "", {})
    if not responsible:
        responsible = users.get(row.get("ОтветственноеЛицо_Key") or "", {})
    manager_fio = user_description(manager)

    return {
        "document_ref": parent.get("Ref_Key"),
        "line_number": row.get("LineNumber"),
        "document_number": parent.get("Number"),
        "document_date": parent.get("Date"),
        "subject": parent.get("ОЧем") or "",
        "status": parent.get("Статус"),
        "activity": row.get("Мероприятие") or "",
        "responsible": person_description(responsible) or user_description(responsible),
        "manager": manager_fio,
        "secretary": user_description(secretary),
        "due_date": row.get("СрокИсполнения"),
        "overdue": overdue,
        "has_file": "Нет",
        "priority": compute_priority(
            due_date=due_date,
            confirmed=overdue,
            completed=overdue,
            has_file=False,
            manager=manager_fio,
            now=now,
        ),
    }


def fetch_limited_rows(
    session: requests.Session,
    config: ODataConfig,
    *,
    entity: str,
    odata_filter: str,
    limit: int,
    orderby: str | None = None,
) -> list[dict[str, Any]]:
    order = f"&$orderby={quote(orderby, safe=', ')}" if orderby else ""
    url = (
        f"{entity_url(config.url, entity)}"
        f"?$filter={quote(odata_filter, safe='')}"
        f"{order}&$top={limit}&$format=json"
    )
    data = odata_get_json(session, url, timeout=config.timeout)
    return data.get("value") or []


def fetch_porucheniya_rows(
    session: requests.Session,
    config: ODataConfig,
    *,
    period_start: date,
    period_end: date,
    limit: int,
) -> list[dict[str, Any]]:
    odata_filter = (
        f"СрокИсполнения ge {format_odata_datetime(period_start)} "
        f"and СрокИсполнения le {format_odata_datetime(period_end, end=True)}"
    )
    return fetch_limited_rows(
        session,
        config,
        entity=PORUCHENIYA_TABULAR,
        odata_filter=odata_filter,
        limit=limit,
        orderby="СрокИсполнения desc",
    )


def collect_lookup_keys(
    rows: list[dict[str, Any]],
    parents: dict[str, dict[str, Any]],
) -> tuple[set[str], set[str]]:
    user_keys: set[str] = set()
    person_keys: set[str] = set()

    for row in rows:
        parent = parents.get(row.get("Ref_Key") or "", {})
        if not is_empty_key(parent.get("Руководитель_Key")):
            user_keys.add(parent["Руководитель_Key"])
        if not is_empty_key(parent.get("СекретарьРК_Key")):
            user_keys.add(parent["СекретарьРК_Key"])
        if not is_empty_key(row.get("ОтветственноеЛицо_Key")):
            person_keys.add(row["ОтветственноеЛицо_Key"])
            user_keys.add(row["ОтветственноеЛицо_Key"])

    return user_keys, person_keys


def query_porucheniya(
    *,
    period_start: date | str | None = None,
    period_end: date | str | None = None,
    limit: int = 500,
    config: ODataConfig = CONFIG,
) -> dict[str, Any]:
    if limit < 1:
        raise ValueError("limit must be >= 1")

    end = parse_input_date(period_end, default=date.today())
    start = parse_input_date(period_start, default=end - timedelta(days=90))
    if start > end:
        raise ValueError("period_start не может быть позже period_end")

    session = create_session(config)
    now = datetime.now().replace(microsecond=0)

    tabular_rows = fetch_porucheniya_rows(
        session,
        config,
        period_start=start,
        period_end=end,
        limit=limit,
    )
    parent_keys = {row.get("Ref_Key") for row in tabular_rows if row.get("Ref_Key")}
    parents = load_documents_for_keys(
        session,
        parent_keys,
        entity=PORUCHENIYA_DOCUMENT,
        config=config,
    )
    user_keys, person_keys = collect_lookup_keys(tabular_rows, parents)
    users = load_users_for_keys(session, user_keys, config=config)
    persons = load_persons_for_keys(session, person_keys, config=config)

    items: list[dict[str, Any]] = []
    for row in tabular_rows:
        parent = parents.get(row.get("Ref_Key") or "", {})
        if not parent:
            continue
        items.append(
            normalize_poruchenie_row(row, parent, users=users, persons=persons, now=now)
        )

    return {
        "document_entity": PORUCHENIYA_DOCUMENT,
        "tabular_entity": PORUCHENIYA_TABULAR,
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),
        "limit": limit,
        "count": len(items),
        "selection_method": f"OData: {PORUCHENIYA_TABULAR} by СрокИсполнения",
        "items": items,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Получить поручения из 1С через OData.",
    )
    parser.add_argument("--start", help="Начало периода (YYYY-MM-DD)")
    parser.add_argument("--end", help="Конец периода (YYYY-MM-DD)")
    parser.add_argument("--limit", type=int, default=500, help="Максимум строк")
    parser.add_argument("-o", "--output", help="Путь к JSON-файлу результата")
    return parser


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    args = build_parser().parse_args(argv)
    try:
        result = query_porucheniya(
            period_start=args.start,
            period_end=args.end,
            limit=args.limit,
        )
    except (requests.RequestException, RuntimeError, ValueError) as error:
        print(f"Ошибка: {error}", file=sys.stderr)
        return 1

    text = json.dumps(result, ensure_ascii=False, indent=2, default=str)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as file:
            file.write(text)
        print(f"Сохранено: {args.output}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
