from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any
from urllib.parse import quote

import requests

from app.integrations.onec_odata import fetch_all
from app.tools.onec.connection import CONFIG, ODataConfig
from app.tools.onec.get_meetings import entity_url
from app.tools.onec.lookup_user_ref import is_empty_key

HR_ENTITY = "InformationRegister_КадроваяИсторияСотрудников_RecordType"
STRUCTURE_ENTITY = "Catalog_СтруктураПредприятия"
ORG_DEPT_ENTITY = "Catalog_ПодразделенияОрганизаций"
EMPTY = "00000000-0000-0000-0000-000000000000"
EMPTY_DATE = "0001-01-01T00:00:00"


def department_leaf_name(value: str | None) -> str:
    """Возвращает последнее подразделение из иерархического пути «A / B / C»."""
    text = (value or "").strip()
    if not text or "/" not in text:
        return text
    parts = [part.strip() for part in text.split("/") if part.strip()]
    return parts[-1] if parts else text


def _normalize_text(value: str | None) -> str:
    value = (value or "").lower().replace("ё", "е")
    return " ".join("".join(ch if ch.isalnum() else " " for ch in value).split())


def _is_empty_date(value: str | None) -> bool:
    return not value or value.startswith(EMPTY_DATE)


def _is_future_or_empty(value: str | None) -> bool:
    if _is_empty_date(value):
        return True
    return value[:19] >= datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def _structure_path(key: str, structure: dict[str, dict[str, Any]]) -> str:
    parts: list[str] = []
    seen: set[str] = set()
    current = key
    while not is_empty_key(current) and current not in seen and current in structure:
        seen.add(current)
        row = structure[current]
        name = (row.get("Description") or "").strip()
        if name:
            parts.append(name)
        current = row.get("Parent_Key") or EMPTY
    return " / ".join(reversed(parts))


def _load_hierarchy(
    session: requests.Session,
    entity: str,
    *,
    config: ODataConfig,
) -> dict[str, dict[str, Any]]:
    url = (
        f"{entity_url(config.url, entity)}"
        f"?$format=json"
        f"&$select={quote('Ref_Key,Description,Parent_Key,DeletionMark', safe=',_')}"
    )
    result: dict[str, dict[str, Any]] = {}
    for row in fetch_all(session, url, page=500, timeout=config.timeout):
        key = row.get("Ref_Key")
        if key and not row.get("DeletionMark"):
            result[key] = row
    return result


def _build_name_index(hierarchy: dict[str, dict[str, Any]]) -> dict[str, list[str]]:
    index: dict[str, list[str]] = defaultdict(list)
    for key, row in hierarchy.items():
        name = _normalize_text(row.get("Description"))
        if name:
            index[name].append(key)
    return index


def _resolve_enterprise_dept_key(
    dept_key: str,
    structure: dict[str, dict[str, Any]],
    org_depts: dict[str, dict[str, Any]],
    structure_name_index: dict[str, list[str]],
) -> str | None:
    if dept_key in structure:
        return dept_key
    org_dept = org_depts.get(dept_key)
    if not org_dept:
        return None
    candidates = structure_name_index.get(_normalize_text(org_dept.get("Description")), [])
    if not candidates:
        return None
    return sorted(candidates, key=lambda key: _structure_path(key, structure))[0]


def _resolve_department_display(
    dept_key: str,
    *,
    structure: dict[str, dict[str, Any]],
    org_depts: dict[str, dict[str, Any]],
    structure_name_index: dict[str, list[str]],
) -> str:
    if is_empty_key(dept_key):
        return ""
    enterprise_key = _resolve_enterprise_dept_key(
        dept_key,
        structure,
        org_depts,
        structure_name_index,
    )
    if enterprise_key:
        return department_leaf_name(_structure_path(enterprise_key, structure))
    if dept_key in org_depts:
        return department_leaf_name((org_depts[dept_key].get("Description") or "").strip())
    return ""


def person_key_for_responsible(
    responsible_key: str | None,
    *,
    users: dict[str, dict[str, Any]],
    persons: dict[str, dict[str, Any]],
) -> str | None:
    if is_empty_key(responsible_key):
        return None
    if responsible_key in persons:
        return responsible_key
    user = users.get(responsible_key or "", {})
    person_key = user.get("ФизическоеЛицо_Key")
    if not is_empty_key(person_key):
        return person_key
    return None


def _load_latest_hr_for_persons(
    session: requests.Session,
    person_keys: set[str],
    *,
    config: ODataConfig,
) -> dict[str, dict[str, Any]]:
    keys = [key for key in person_keys if not is_empty_key(key)]
    if not keys:
        return {}

    latest: dict[str, dict[str, Any]] = {}
    chunk_size = 8
    for offset in range(0, len(keys), chunk_size):
        chunk = keys[offset : offset + chunk_size]
        person_filter = " or ".join(f"ФизическоеЛицо_Key eq guid'{key}'" for key in chunk)
        filter_expr = f"Active eq true and ({person_filter})"
        url = (
            f"{entity_url(config.url, HR_ENTITY)}"
            f"?$filter={quote(filter_expr, safe='')}"
            f"&$select={quote('Period,ФизическоеЛицо_Key,Подразделение_Key,ДействуетДо', safe=',_')}"
            f"&$format=json"
        )
        for row in fetch_all(session, url, page=200, timeout=config.timeout):
            if not _is_future_or_empty(row.get("ДействуетДо")):
                continue
            person_key = row.get("ФизическоеЛицо_Key")
            if is_empty_key(person_key):
                continue
            period = row.get("Period") or ""
            current = latest.get(person_key)
            if current is None or period > (current.get("Period") or ""):
                latest[person_key] = row
    return latest


def resolve_department_key_for_manager_fio(
    session: requests.Session,
    manager_fio: str,
    *,
    config: ODataConfig = CONFIG,
) -> str | None:
    from app.tools.onec.lookup_user_ref import load_persons_for_keys, resolve_user_by_fio

    user_ref, _, users = resolve_user_by_fio(session, manager_fio, config=config)
    user_row = next((user for user in users if user.get("Ref_Key") == user_ref), {})
    person_keys = {
        user_row.get("ФизическоеЛицо_Key")
        for user in users
        if user.get("ФизическоеЛицо_Key") and not is_empty_key(user.get("ФизическоеЛицо_Key"))
    }
    persons = load_persons_for_keys(session, person_keys, config=config)
    person_key = person_key_for_responsible(
        user_ref,
        users={user_ref: user_row},
        persons=persons,
    )
    if person_key:
        hr_rows = _load_latest_hr_for_persons(session, {person_key}, config=config)
        hr_row = hr_rows.get(person_key)
        if hr_row:
            dept_key = hr_row.get("Подразделение_Key")
            if not is_empty_key(dept_key):
                return str(dept_key).strip()

    dept_key = user_row.get("Подразделение_Key")
    if is_empty_key(dept_key):
        return None
    return str(dept_key).strip()


def load_departments_for_responsible_keys(
    session: requests.Session,
    responsible_keys: set[str],
    *,
    users: dict[str, dict[str, Any]],
    persons: dict[str, dict[str, Any]],
    config: ODataConfig = CONFIG,
) -> dict[str, str]:
    """Подразделение исполнителя по ключу ОтветственноеЛицо_Key / пользователя."""
    person_keys: set[str] = set()
    responsible_to_person: dict[str, str] = {}
    for responsible_key in responsible_keys:
        person_key = person_key_for_responsible(
            responsible_key,
            users=users,
            persons=persons,
        )
        if person_key:
            person_keys.add(person_key)
            responsible_to_person[responsible_key] = person_key

    if not person_keys:
        return {}

    hr_rows = _load_latest_hr_for_persons(session, person_keys, config=config)
    structure = _load_hierarchy(session, STRUCTURE_ENTITY, config=config)
    org_depts = _load_hierarchy(session, ORG_DEPT_ENTITY, config=config)
    structure_name_index = _build_name_index(structure)

    person_department: dict[str, str] = {}
    for person_key, hr_row in hr_rows.items():
        dept_key = hr_row.get("Подразделение_Key")
        person_department[person_key] = _resolve_department_display(
            dept_key or "",
            structure=structure,
            org_depts=org_depts,
            structure_name_index=structure_name_index,
        )

    departments: dict[str, str] = {}
    for responsible_key in responsible_keys:
        person_key = responsible_to_person.get(responsible_key or "")
        if person_key:
            departments[responsible_key] = person_department.get(person_key, "")
            continue
        user = users.get(responsible_key or "", {})
        dept_key = user.get("Подразделение_Key")
        if not is_empty_key(dept_key):
            departments[responsible_key] = _resolve_department_display(
                dept_key,
                structure=structure,
                org_depts=org_depts,
                structure_name_index=structure_name_index,
            )
        else:
            departments[responsible_key] = ""

    return departments
