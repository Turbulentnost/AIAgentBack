"""
Поручения и задачи протоколов из 1С:ERP (OData).

Читает:
- Document_ТД_Поручения (поручения) за период по дате документа, далее все мероприятия;
- InformationRegister_ТД_ЗадачиПротоколов (задачи протоколов) за период по сроку исполнения.

Обе выборки фильтруются по полю Руководитель в шапке документа-основания.
Приоритет рассчитывается по правилам отчёта 1С.

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
from app.tools.onec.lookup_person_department import load_departments_for_responsible_keys
from app.tools.onec.lookup_user_ref import (
    USER_CATALOG,
    is_empty_key,
    load_persons_for_keys,
    normalize_name,
    resolve_user_by_fio,
)

PORUCHENIYA_TABULAR = "Document_ТД_Поручения_Поручения"
PORUCHENIYA_DOCUMENT = "Document_ТД_Поручения"
PROTOCOL_TASKS_REGISTER = "InformationRegister_ТД_ЗадачиПротоколов"
PROTOCOL_DOCUMENT = "Document_ТД_Протокол"
TOPIC_CATALOG = "Catalog_ТД_ТемыСовещаний"
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


def fio_matches(left: str | None, right: str | None) -> bool:
    if not left or not right:
        return False
    return normalize_name(left) == normalize_name(right)


def resolve_manager_keys_for_fio(
    session: requests.Session,
    manager_fio: str,
    *,
    config: ODataConfig = CONFIG,
) -> set[str]:
    """Ключи 1С пользователя/физлица для фильтра по руководителю поручения."""
    user_ref, _, users = resolve_user_by_fio(session, manager_fio.strip(), config=config)
    keys = {user_ref}
    for user in users:
        person_key = user.get("ФизическоеЛицо_Key")
        if person_key and not is_empty_key(person_key):
            keys.add(person_key)
    return keys


def build_manager_filter(manager_keys: set[str]) -> str:
    parts = [
        f"Руководитель_Key eq guid'{key}'"
        for key in manager_keys
        if not is_empty_key(key)
    ]
    if not parts:
        raise ValueError("Не удалось определить ключ руководителя в 1С")
    if len(parts) == 1:
        return parts[0]
    return f"({' or '.join(parts)})"


# alias для тестов и обратной совместимости импортов
resolve_author_keys_for_fio = resolve_manager_keys_for_fio
build_author_filter = build_manager_filter


def entity_description(row: dict[str, Any] | None) -> str:
    if not row:
        return ""
    return (row.get("Description") or "").strip()


def row_has_file(row: dict[str, Any]) -> bool:
    if (row.get("Файл_Base64Data") or "").strip():
        return True
    file_value = row.get("Файл")
    if isinstance(file_value, str) and file_value.strip() and not file_value.startswith(EMPTY_DATE[:10]):
        return True
    return False


def format_has_file(value: bool) -> str:
    return "Да" if value else "Нет"


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
            f"&$select={quote('Ref_Key,Description,DeletionMark,ФизическоеЛицо_Key,Подразделение_Key', safe=',_')}"
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


def normalize_poruchenie_task_row(
    row: dict[str, Any],
    *,
    users: dict[str, dict[str, Any]],
    persons: dict[str, dict[str, Any]],
    manager_fio: str,
    now: datetime,
    department: str = "",
) -> dict[str, Any]:
    due_date = parse_onec_datetime(row.get("СрокИсполнения"))
    overdue = due_date is not None and due_date < now
    responsible = persons.get(row.get("ОтветственноеЛицо_Key") or "", {})
    if not responsible:
        responsible = users.get(row.get("ОтветственноеЛицо_Key") or "", {})

    return {
        "item_type": "poruchenie_task",
        "line_number": row.get("LineNumber"),
        "activity": row.get("Мероприятие") or "",
        "responsible": person_description(responsible) or user_description(responsible),
        "department": department,
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


def normalize_poruchenie_document(
    parent: dict[str, Any],
    *,
    users: dict[str, dict[str, Any]],
    tasks: list[dict[str, Any]],
) -> dict[str, Any]:
    manager = users.get(parent.get("Руководитель_Key") or "", {})
    secretary = users.get(parent.get("СекретарьРК_Key") or "", {})
    reporter = users.get(parent.get("КтоДоложитОЗавершенииМероприятий_Key") or "", {})

    return {
        "item_type": "poruchenie",
        "document_ref": parent.get("Ref_Key"),
        "document_number": parent.get("Number"),
        "document_date": parent.get("Date"),
        "subject": parent.get("ОЧем") or "",
        "status": parent.get("Статус"),
        "basis": parent.get("Основание") or "",
        "manager": user_description(manager),
        "secretary": user_description(secretary),
        "reviewer": user_description(secretary),
        "reporter": user_description(reporter),
        "tasks_count": len(tasks),
        "tasks": tasks,
    }


def group_porucheniya_documents(
    parents: dict[str, dict[str, Any]],
    tabular_rows: list[dict[str, Any]],
    *,
    users: dict[str, dict[str, Any]],
    persons: dict[str, dict[str, Any]],
    departments_by_responsible: dict[str, str],
    now: datetime,
) -> list[dict[str, Any]]:
    rows_by_document: dict[str, list[dict[str, Any]]] = {}
    for row in tabular_rows:
        document_ref = row.get("Ref_Key") or ""
        if document_ref:
            rows_by_document.setdefault(document_ref, []).append(row)

    documents: list[dict[str, Any]] = []
    sorted_refs = sorted(
        parents.keys(),
        key=lambda ref: parse_onec_datetime(parents[ref].get("Date")) or datetime.min,
        reverse=True,
    )
    for document_ref in sorted_refs:
        parent = parents[document_ref]
        manager_fio = user_description(users.get(parent.get("Руководитель_Key") or "", {}))
        tasks: list[dict[str, Any]] = []
        for row in rows_by_document.get(document_ref, []):
            responsible_key = row.get("ОтветственноеЛицо_Key") or ""
            tasks.append(
                normalize_poruchenie_task_row(
                    row,
                    users=users,
                    persons=persons,
                    manager_fio=manager_fio,
                    now=now,
                    department=departments_by_responsible.get(responsible_key, ""),
                )
            )
        documents.append(
            normalize_poruchenie_document(
                parent,
                users=users,
                tasks=sort_items_by_due_date(tasks),
            )
        )

    return documents


def sort_documents_by_latest_due_date(documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def sort_key(document: dict[str, Any]) -> datetime:
        latest = datetime.min
        for task in document.get("tasks") or []:
            parsed = parse_onec_datetime(task.get("due_date"))
            if parsed and parsed > latest:
                latest = parsed
        if latest != datetime.min:
            return latest
        return parse_onec_datetime(document.get("document_date")) or datetime.min

    return sorted(documents, key=sort_key, reverse=True)


def flatten_protocol_tasks(protocol_documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for document in protocol_documents:
        for task in document.get("tasks") or []:
            items.append(
                {
                    **task,
                    "protocol_ref": document.get("document_ref"),
                    "document_number": document.get("document_number"),
                    "document_date": document.get("document_date"),
                    "topic": document.get("topic"),
                    "subject": document.get("subject"),
                    "status": document.get("status"),
                    "manager": document.get("manager"),
                    "reviewer": document.get("reviewer"),
                }
            )
    return items


def flatten_all_tasks(
    porucheniya_documents: list[dict[str, Any]],
    protocol_documents: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for document in porucheniya_documents:
        for task in document.get("tasks") or []:
            items.append(
                {
                    **task,
                    "document_ref": document.get("document_ref"),
                    "document_number": document.get("document_number"),
                    "document_date": document.get("document_date"),
                    "subject": document.get("subject"),
                    "manager": document.get("manager"),
                    "secretary": document.get("secretary"),
                }
            )
    items.extend(flatten_protocol_tasks(protocol_documents))
    return sort_items_by_due_date(items)


def filter_protocol_documents_by_manager_fio(
    documents: list[dict[str, Any]],
    manager_fio: str,
) -> list[dict[str, Any]]:
    normalized_manager = manager_fio.strip()
    return [
        document
        for document in documents
        if fio_matches(document.get("manager"), normalized_manager)
    ]


def filter_porucheniya_documents_by_manager_fio(
    documents: list[dict[str, Any]],
    manager_fio: str,
) -> list[dict[str, Any]]:
    normalized_manager = manager_fio.strip()
    return [
        document
        for document in documents
        if fio_matches(document.get("manager"), normalized_manager)
    ]


# alias для обратной совместимости тестов
normalize_poruchenie_row = normalize_poruchenie_task_row


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


MANAGER_FETCH_POOL_MULTIPLIER = 5
MANAGER_FETCH_POOL_MIN = 100
MANAGER_FETCH_POOL_MAX = 1000


def build_task_period_filter(period_start: date, period_end: date) -> str:
    return (
        f"СрокИсполнения ge {format_odata_datetime(period_start)} "
        f"and СрокИсполнения le {format_odata_datetime(period_end, end=True)}"
    )


def build_document_period_filter(period_start: date, period_end: date) -> str:
    return (
        f"Date ge {format_odata_datetime(period_start)} "
        f"and Date le {format_odata_datetime(period_end, end=True)} "
        f"and DeletionMark eq false"
    )


build_period_filter = build_task_period_filter


def fetch_porucheniya_documents(
    session: requests.Session,
    config: ODataConfig,
    *,
    period_start: date,
    period_end: date,
    limit: int,
    manager_keys: set[str] | None = None,
) -> list[dict[str, Any]]:
    odata_filter = build_document_period_filter(period_start, period_end)
    if manager_keys:
        odata_filter = f"{odata_filter} and {build_manager_filter(manager_keys)}"
    return fetch_limited_rows(
        session,
        config,
        entity=PORUCHENIYA_DOCUMENT,
        odata_filter=odata_filter,
        limit=limit,
        orderby="Date desc",
    )


def fetch_tabular_rows_for_document_keys(
    session: requests.Session,
    config: ODataConfig,
    document_keys: set[str],
) -> list[dict[str, Any]]:
    keys = [key for key in document_keys if not is_empty_key(key)]
    if not keys:
        return []

    rows: list[dict[str, Any]] = []
    chunk_size = 10
    for offset in range(0, len(keys), chunk_size):
        chunk = keys[offset : offset + chunk_size]
        filter_expr = " or ".join(f"Ref_Key eq guid'{key}'" for key in chunk)
        url = (
            f"{entity_url(config.url, PORUCHENIYA_TABULAR)}"
            f"?$filter={quote(filter_expr, safe='')}"
            f"&$orderby=LineNumber asc&$format=json"
        )
        for row in fetch_all(session, url, page=100, timeout=config.timeout):
            rows.append(row)
    return rows


def fetch_porucheniya_rows(
    session: requests.Session,
    config: ODataConfig,
    *,
    period_start: date,
    period_end: date,
    limit: int,
) -> list[dict[str, Any]]:
    return fetch_limited_rows(
        session,
        config,
        entity=PORUCHENIYA_TABULAR,
        odata_filter=build_task_period_filter(period_start, period_end),
        limit=limit,
        orderby="СрокИсполнения desc",
    )


def fetch_protocol_documents(
    session: requests.Session,
    config: ODataConfig,
    *,
    period_start: date,
    period_end: date,
    limit: int,
    manager_keys: set[str] | None = None,
) -> list[dict[str, Any]]:
    odata_filter = build_document_period_filter(period_start, period_end)
    if manager_keys:
        odata_filter = f"{odata_filter} and {build_manager_filter(manager_keys)}"
    return fetch_limited_rows(
        session,
        config,
        entity=PROTOCOL_DOCUMENT,
        odata_filter=odata_filter,
        limit=limit,
        orderby="Date desc",
    )


def fetch_register_rows_for_protocol_keys(
    session: requests.Session,
    config: ODataConfig,
    protocol_keys: set[str],
) -> list[dict[str, Any]]:
    keys = [key for key in protocol_keys if not is_empty_key(key)]
    if not keys:
        return []

    rows: list[dict[str, Any]] = []
    chunk_size = 8
    for offset in range(0, len(keys), chunk_size):
        chunk = keys[offset : offset + chunk_size]
        filter_expr = " or ".join(f"Протокол_Key eq guid'{key}'" for key in chunk)
        url = (
            f"{entity_url(config.url, PROTOCOL_TASKS_REGISTER)}"
            f"?$filter={quote(filter_expr, safe='')}"
            f"&$orderby=НомерПунктаПротокола asc&$format=json"
        )
        for row in fetch_all(session, url, page=200, timeout=config.timeout):
            rows.append(row)
    return rows


def fetch_protocol_task_rows(
    session: requests.Session,
    config: ODataConfig,
    *,
    period_start: date,
    period_end: date,
    limit: int,
) -> list[dict[str, Any]]:
    return fetch_limited_rows(
        session,
        config,
        entity=PROTOCOL_TASKS_REGISTER,
        odata_filter=build_task_period_filter(period_start, period_end),
        limit=limit,
        orderby="СрокИсполнения desc",
    )


def filter_rows_by_manager(
    rows: list[dict[str, Any]],
    parents: dict[str, dict[str, Any]],
    manager_keys: set[str],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    filtered: list[dict[str, Any]] = []
    for row in rows:
        parent = parents.get(row.get("Ref_Key") or "", {})
        if parent.get("Руководитель_Key") in manager_keys:
            filtered.append(row)
        if len(filtered) >= limit:
            break
    return filtered


def filter_protocol_tasks_by_manager(
    rows: list[dict[str, Any]],
    protocols: dict[str, dict[str, Any]],
    manager_keys: set[str],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    filtered: list[dict[str, Any]] = []
    for row in rows:
        protocol = protocols.get(row.get("Протокол_Key") or "", {})
        if protocol.get("Руководитель_Key") in manager_keys:
            filtered.append(row)
        if len(filtered) >= limit:
            break
    return filtered


def _collect_protocol_header_lookup_keys(
    protocol: dict[str, Any],
    *,
    user_keys: set[str],
    person_keys: set[str],
    topic_keys: set[str],
) -> None:
    for key_name in ("Руководитель_Key", "Ответственный_Key", "Подготовил_Key"):
        key = protocol.get(key_name)
        if not is_empty_key(key):
            user_keys.add(key)
        if key_name == "Ответственный_Key" and not is_empty_key(key):
            person_keys.add(key)
    topic_key = protocol.get("ТемаСовещания_Key")
    if not is_empty_key(topic_key):
        topic_keys.add(topic_key)


def collect_protocol_lookup_keys(
    rows: list[dict[str, Any]],
    protocols: dict[str, dict[str, Any]],
) -> tuple[set[str], set[str], set[str]]:
    user_keys: set[str] = set()
    person_keys: set[str] = set()
    topic_keys: set[str] = set()

    for protocol in protocols.values():
        _collect_protocol_header_lookup_keys(
            protocol,
            user_keys=user_keys,
            person_keys=person_keys,
            topic_keys=topic_keys,
        )

    for row in rows:
        protocol = protocols.get(row.get("Протокол_Key") or "", {})
        for key_name in ("Ответственный_Key", "Автор_Key"):
            key = row.get(key_name)
            if not is_empty_key(key):
                user_keys.add(key)
                person_keys.add(key)
        topic_key = row.get("ТемаСовещания_Key") or protocol.get("ТемаСовещания_Key")
        if not is_empty_key(topic_key):
            topic_keys.add(topic_key)

    return user_keys, person_keys, topic_keys


def normalize_protocol_register_task_row(
    row: dict[str, Any],
    protocol: dict[str, Any],
    *,
    users: dict[str, dict[str, Any]],
    persons: dict[str, dict[str, Any]],
    manager_fio: str,
    now: datetime,
    department: str = "",
) -> dict[str, Any]:
    due_date = parse_onec_datetime(row.get("СрокИсполнения"))
    completed = bool(row.get("Выполнена"))
    confirmed = bool(row.get("Подтверждена"))
    overdue = due_date is not None and due_date < now and not completed
    responsible = persons.get(row.get("Ответственный_Key") or "", {})
    if not responsible:
        responsible = users.get(row.get("Ответственный_Key") or "", {})
    has_file = row_has_file(row)

    return {
        "item_type": "protocol_task",
        "task_id": row.get("ИдентификаторЗадачи"),
        "topic_key": row.get("ТемаСовещания_Key") or protocol.get("ТемаСовещания_Key"),
        "protocol_item_number": row.get("НомерПунктаПротокола"),
        "activity": row.get("Задача") or "",
        "responsible": person_description(responsible) or user_description(responsible),
        "department": department,
        "assigned_date": row.get("ДатаПостановкиЗадачи"),
        "due_date": row.get("СрокИсполнения"),
        "completed_date": row.get("ДатаИсполнения"),
        "sent": bool(row.get("Отправлена")),
        "completed": completed,
        "confirmed": confirmed,
        "comment": row.get("Комментарий") or "",
        "note": row.get("Примечание") or "",
        "overdue": overdue,
        "has_file": format_has_file(has_file),
        "priority": compute_priority(
            due_date=due_date,
            confirmed=confirmed,
            completed=completed,
            has_file=has_file,
            manager=manager_fio,
            now=now,
        ),
    }


def normalize_protocol_document(
    protocol: dict[str, Any],
    *,
    users: dict[str, dict[str, Any]],
    persons: dict[str, dict[str, Any]],
    topics: dict[str, dict[str, Any]],
    tasks: list[dict[str, Any]],
) -> dict[str, Any]:
    manager = users.get(protocol.get("Руководитель_Key") or "", {})
    reviewer_entity = persons.get(protocol.get("Ответственный_Key") or "", {})
    if not reviewer_entity:
        reviewer_entity = users.get(protocol.get("Ответственный_Key") or "", {})
    topic = topics.get(protocol.get("ТемаСовещания_Key") or "", {})
    topic_name = entity_description(topic)

    return {
        "item_type": "protocol",
        "document_ref": protocol.get("Ref_Key"),
        "document_number": protocol.get("Number"),
        "document_date": protocol.get("Date"),
        "topic": topic_name,
        "subject": topic_name,
        "status": protocol.get("Статус"),
        "manager": user_description(manager),
        "reviewer": person_description(reviewer_entity) or user_description(reviewer_entity),
        "tasks_count": len(tasks),
        "tasks": tasks,
    }


def group_protocol_documents(
    protocols: dict[str, dict[str, Any]],
    register_rows: list[dict[str, Any]],
    *,
    users: dict[str, dict[str, Any]],
    persons: dict[str, dict[str, Any]],
    topics: dict[str, dict[str, Any]],
    departments_by_responsible: dict[str, str],
    now: datetime,
) -> list[dict[str, Any]]:
    rows_by_protocol: dict[str, list[dict[str, Any]]] = {}
    for row in register_rows:
        protocol_ref = row.get("Протокол_Key") or ""
        if protocol_ref:
            rows_by_protocol.setdefault(protocol_ref, []).append(row)

    documents: list[dict[str, Any]] = []
    sorted_refs = sorted(
        protocols.keys(),
        key=lambda ref: parse_onec_datetime(protocols[ref].get("Date")) or datetime.min,
        reverse=True,
    )
    for protocol_ref in sorted_refs:
        protocol = protocols[protocol_ref]
        manager_fio = user_description(users.get(protocol.get("Руководитель_Key") or "", {}))
        tasks: list[dict[str, Any]] = []
        for row in rows_by_protocol.get(protocol_ref, []):
            responsible_key = row.get("Ответственный_Key") or ""
            tasks.append(
                normalize_protocol_register_task_row(
                    row,
                    protocol,
                    users=users,
                    persons=persons,
                    manager_fio=manager_fio,
                    now=now,
                    department=departments_by_responsible.get(responsible_key, ""),
                )
            )
        documents.append(
            normalize_protocol_document(
                protocol,
                users=users,
                persons=persons,
                topics=topics,
                tasks=sort_items_by_due_date(tasks),
            )
        )

    return documents


def normalize_protocol_task_row(
    row: dict[str, Any],
    protocol: dict[str, Any],
    *,
    topics: dict[str, dict[str, Any]],
    users: dict[str, dict[str, Any]],
    persons: dict[str, dict[str, Any]],
    now: datetime,
    department: str = "",
) -> dict[str, Any]:
    """Плоское представление задачи протокола (legacy / flatten)."""
    manager_fio = user_description(users.get(protocol.get("Руководитель_Key") or "", {}))
    task = normalize_protocol_register_task_row(
        row,
        protocol,
        users=users,
        persons=persons,
        manager_fio=manager_fio,
        now=now,
        department=department,
    )
    topic_key = task.get("topic_key") or protocol.get("ТемаСовещания_Key")
    topic_name = entity_description(topics.get(topic_key or "", {}))
    author = users.get(row.get("Автор_Key") or "", {})

    return {
        **task,
        "protocol_ref": protocol.get("Ref_Key") or row.get("Протокол_Key"),
        "document_number": protocol.get("Number"),
        "document_date": protocol.get("Date"),
        "topic": topic_name,
        "subject": topic_name,
        "status": protocol.get("Статус"),
        "author": user_description(author),
        "manager": manager_fio,
    }


def sort_items_by_due_date(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def sort_key(item: dict[str, Any]) -> datetime:
        parsed = parse_onec_datetime(item.get("due_date"))
        return parsed or datetime.min

    return sorted(items, key=sort_key, reverse=True)


def filter_items_by_manager_fio(items: list[dict[str, Any]], manager_fio: str) -> list[dict[str, Any]]:
    normalized_manager = manager_fio.strip()
    return [
        item
        for item in items
        if fio_matches(item.get("manager"), normalized_manager)
        or fio_matches(item.get("author"), normalized_manager)
    ]


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
        if not is_empty_key(parent.get("КтоДоложитОЗавершенииМероприятий_Key")):
            user_keys.add(parent["КтоДоложитОЗавершенииМероприятий_Key"])
        if not is_empty_key(row.get("ОтветственноеЛицо_Key")):
            person_keys.add(row["ОтветственноеЛицо_Key"])
            user_keys.add(row["ОтветственноеЛицо_Key"])

    return user_keys, person_keys


def load_porucheniya_documents(
    session: requests.Session,
    config: ODataConfig,
    *,
    period_start: date,
    period_end: date,
    limit: int,
    fetch_limit: int,
    filter_manager_keys: set[str] | None,
    now: datetime,
) -> list[dict[str, Any]]:
    del fetch_limit
    parent_rows = fetch_porucheniya_documents(
        session,
        config,
        period_start=period_start,
        period_end=period_end,
        limit=limit,
        manager_keys=filter_manager_keys,
    )
    parents = {
        row["Ref_Key"]: row
        for row in parent_rows
        if row.get("Ref_Key")
    }
    tabular_rows = fetch_tabular_rows_for_document_keys(
        session,
        config,
        set(parents.keys()),
    )
    user_keys, person_keys = collect_lookup_keys(tabular_rows, parents)
    users = load_users_for_keys(session, user_keys, config=config)
    persons = load_persons_for_keys(session, person_keys, config=config)
    responsible_keys = {
        row.get("ОтветственноеЛицо_Key")
        for row in tabular_rows
        if not is_empty_key(row.get("ОтветственноеЛицо_Key"))
    }
    departments_by_responsible = load_departments_for_responsible_keys(
        session,
        responsible_keys,
        users=users,
        persons=persons,
        config=config,
    )

    return group_porucheniya_documents(
        parents,
        tabular_rows,
        users=users,
        persons=persons,
        departments_by_responsible=departments_by_responsible,
        now=now,
    )


load_porucheniya_items = load_porucheniya_documents


def load_protocol_documents(
    session: requests.Session,
    config: ODataConfig,
    *,
    period_start: date,
    period_end: date,
    limit: int,
    fetch_limit: int,
    filter_manager_keys: set[str] | None,
    now: datetime,
) -> list[dict[str, Any]]:
    del fetch_limit
    parent_rows = fetch_protocol_documents(
        session,
        config,
        period_start=period_start,
        period_end=period_end,
        limit=limit,
        manager_keys=filter_manager_keys,
    )
    protocols = {
        row["Ref_Key"]: row
        for row in parent_rows
        if row.get("Ref_Key")
    }
    register_rows = fetch_register_rows_for_protocol_keys(
        session,
        config,
        set(protocols.keys()),
    )
    user_keys, person_keys, topic_keys = collect_protocol_lookup_keys(register_rows, protocols)
    users = load_users_for_keys(session, user_keys, config=config)
    persons = load_persons_for_keys(session, person_keys, config=config)
    topics = load_documents_for_keys(
        session,
        topic_keys,
        entity=TOPIC_CATALOG,
        config=config,
    )
    responsible_keys = {
        row.get("Ответственный_Key")
        for row in register_rows
        if not is_empty_key(row.get("Ответственный_Key"))
    }
    departments_by_responsible = load_departments_for_responsible_keys(
        session,
        responsible_keys,
        users=users,
        persons=persons,
        config=config,
    )

    return group_protocol_documents(
        protocols,
        register_rows,
        users=users,
        persons=persons,
        topics=topics,
        departments_by_responsible=departments_by_responsible,
        now=now,
    )


load_protocol_task_items = load_protocol_documents


def resolve_porucheniya_period(
    period_start: date | str | None,
    period_end: date | str | None,
    *,
    today: date | None = None,
) -> tuple[date, date]:
    """Период по умолчанию — вчерашняя дата (один календарный день)."""
    yesterday = (today or date.today()) - timedelta(days=1)
    if period_start is None and period_end is None:
        return yesterday, yesterday
    end = parse_input_date(period_end, default=yesterday)
    start = parse_input_date(period_start, default=end)
    return start, end


def query_porucheniya(
    *,
    period_start: date | str | None = None,
    period_end: date | str | None = None,
    limit: int = 500,
    author_fio: str | None = None,
    config: ODataConfig = CONFIG,
) -> dict[str, Any]:
    if limit < 1:
        raise ValueError("limit must be >= 1")

    start, end = resolve_porucheniya_period(period_start, period_end)
    if start > end:
        raise ValueError("period_start не может быть позже period_end")

    session = create_session(config)
    now = datetime.now().replace(microsecond=0)

    filter_manager_keys: set[str] | None = None
    selection_method = (
        f"OData: {PORUCHENIYA_DOCUMENT} by Date + "
        f"{PROTOCOL_DOCUMENT} by Date"
    )
    fetch_limit = limit
    if author_fio and author_fio.strip():
        filter_manager_keys = resolve_manager_keys_for_fio(session, author_fio, config=config)
        fetch_limit = min(
            max(limit * MANAGER_FETCH_POOL_MULTIPLIER, MANAGER_FETCH_POOL_MIN),
            MANAGER_FETCH_POOL_MAX,
        )
        selection_method = (
            f"OData: {PORUCHENIYA_DOCUMENT} by Date, "
            f"{PROTOCOL_DOCUMENT} by Date, "
            f"filter Руководитель (author_fio={author_fio.strip()})"
        )

    porucheniya_documents = load_porucheniya_documents(
        session,
        config,
        period_start=start,
        period_end=end,
        limit=limit,
        fetch_limit=fetch_limit,
        filter_manager_keys=filter_manager_keys,
        now=now,
    )
    protocol_documents = load_protocol_documents(
        session,
        config,
        period_start=start,
        period_end=end,
        limit=limit,
        fetch_limit=fetch_limit,
        filter_manager_keys=filter_manager_keys,
        now=now,
    )

    if author_fio and author_fio.strip():
        porucheniya_documents = filter_porucheniya_documents_by_manager_fio(
            porucheniya_documents,
            author_fio,
        )
        protocol_documents = filter_protocol_documents_by_manager_fio(
            protocol_documents,
            author_fio,
        )

    porucheniya_tasks_count = sum(
        len(document.get("tasks") or []) for document in porucheniya_documents
    )
    protocol_tasks_count = sum(
        len(document.get("tasks") or []) for document in protocol_documents
    )
    protocol_tasks = flatten_protocol_tasks(protocol_documents)
    items = flatten_all_tasks(porucheniya_documents, protocol_documents)
    counts = {
        "porucheniya_documents": len(porucheniya_documents),
        "porucheniya_tasks": porucheniya_tasks_count,
        "protocol_documents": len(protocol_documents),
        "protocol_tasks": protocol_tasks_count,
        "total_tasks": len(items),
    }

    return {
        "document_entity": PORUCHENIYA_DOCUMENT,
        "tabular_entity": PORUCHENIYA_TABULAR,
        "register_entity": PROTOCOL_TASKS_REGISTER,
        "protocol_entity": PROTOCOL_DOCUMENT,
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),
        "limit": limit,
        "count": counts["total_tasks"],
        "counts": counts,
        "author_fio": author_fio.strip() if author_fio and author_fio.strip() else None,
        "selection_method": selection_method,
        "porucheniya": porucheniya_documents,
        "protocols": protocol_documents,
        "protocol_tasks": protocol_tasks,
        "items": items,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Получить поручения из 1С через OData.",
    )
    parser.add_argument("--start", help="Начало периода (YYYY-MM-DD)")
    parser.add_argument("--end", help="Конец периода (YYYY-MM-DD)")
    parser.add_argument("--limit", type=int, default=500, help="Максимум строк")
    parser.add_argument("--author-fio", help="Фильтр по ФИО руководителя поручения")
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
            author_fio=args.author_fio,
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
