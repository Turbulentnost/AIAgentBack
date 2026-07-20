from __future__ import annotations

from typing import Any
from urllib.parse import quote

import requests

from app.integrations.onec_odata import fetch_all
from app.tools.onec.connection import CONFIG, ODataConfig

USER_CATALOG = "Catalog_Пользователи"
PERSON_CATALOG = "Catalog_ФизическиеЛица"
EMPTY = "00000000-0000-0000-0000-000000000000"


def entity_url(base: str, entity: str) -> str:
    return f"{base.rstrip('/')}/{quote(entity)}"


def normalize_name(value: str | None) -> str:
    value = (value or "").lower().replace("ё", "е")
    return " ".join("".join(ch if ch.isalnum() else " " for ch in value).split())


def normalize_lookup_fio(value: str | None) -> str:
    """Нормализация ФИО перед поиском в 1С (лишние пробелы, точка в конце)."""
    return " ".join((value or "").split()).strip(" .")


def is_empty_key(value: str | None) -> bool:
    return not value or value == EMPTY


def odata_escape(value: str) -> str:
    return value.replace("'", "''")


def load_users(
    session: requests.Session,
    *,
    config: ODataConfig = CONFIG,
) -> list[dict[str, Any]]:
    url = (
        f"{entity_url(config.url, USER_CATALOG)}"
        f"?$filter={quote('DeletionMark eq false', safe='')}"
        f"&$format=json"
    )
    return fetch_all(session, url, page=500, timeout=config.timeout)


def search_users_by_fio(
    session: requests.Session,
    fio: str,
    *,
    config: ODataConfig = CONFIG,
) -> list[dict[str, Any]]:
    """Быстрый поиск пользователей по ФИО через OData $filter (без загрузки всего каталога)."""
    parts = normalize_name(fio).split()
    if not parts:
        return []

    surname = odata_escape(parts[0])
    filter_expr = (
        f"DeletionMark eq false and substringof('{surname}', Description) eq true"
    )
    url = (
        f"{entity_url(config.url, USER_CATALOG)}"
        f"?$filter={quote(filter_expr, safe='')}"
        f"&$select={quote('Ref_Key,Description,ФизическоеЛицо_Key,DeletionMark,Недействителен', safe=',_')}"
        f"&$format=json"
    )
    rows = fetch_all(session, url, page=100, timeout=config.timeout)
    active = [
        row
        for row in rows
        if not row.get("DeletionMark") and not row.get("Недействителен")
    ]
    if len(active) <= 1:
        return active

    key = normalize_name(fio)
    query_parts = key.split()
    if len(query_parts) < 2:
        return active

    filtered: list[dict[str, Any]] = []
    for user in active:
        user_parts = normalize_name(user_fio(user, {})).split()
        if len(user_parts) >= 2 and user_parts[0] == query_parts[0] and user_parts[1] == query_parts[1]:
            filtered.append(user)
    return filtered or active


def load_persons(
    session: requests.Session,
    person_keys: set[str],
    *,
    config: ODataConfig = CONFIG,
) -> dict[str, dict[str, Any]]:
    return load_persons_for_keys(session, person_keys, config=config)


def load_persons_for_keys(
    session: requests.Session,
    person_keys: set[str],
    *,
    config: ODataConfig = CONFIG,
) -> dict[str, dict[str, Any]]:
    keys = [key for key in person_keys if not is_empty_key(key)]
    if not keys:
        return {}

    result: dict[str, dict[str, Any]] = {}
    chunk_size = 15
    for offset in range(0, len(keys), chunk_size):
        chunk = keys[offset : offset + chunk_size]
        filter_expr = " or ".join(f"Ref_Key eq guid'{key}'" for key in chunk)
        url = (
            f"{entity_url(config.url, PERSON_CATALOG)}"
            f"?$filter={quote(filter_expr, safe='')}"
            f"&$select={quote('Ref_Key,Description,ФИО,DeletionMark', safe=',_')}"
            f"&$format=json"
        )
        for row in fetch_all(session, url, page=100, timeout=config.timeout):
            if row.get("Ref_Key") and not row.get("DeletionMark"):
                result[row["Ref_Key"]] = row
    return result


def resolve_user_by_fio(
    session: requests.Session,
    fio: str,
    *,
    config: ODataConfig = CONFIG,
) -> tuple[str, str, list[dict[str, Any]]]:
    """Возвращает (user_ref, resolved_fio, matched_users)."""
    fio = normalize_lookup_fio(fio)
    users = search_users_by_fio(session, fio, config=config)
    if not users:
        raise ValueError(f"Пользователь не найден: «{fio}»")

    person_keys = {
        user.get("ФизическоеЛицо_Key")
        for user in users
        if user.get("ФизическоеЛицо_Key") and not is_empty_key(user.get("ФизическоеЛицо_Key"))
    }
    persons = load_persons_for_keys(session, person_keys, config=config)
    exact_index, ambiguous = build_fio_index(users, persons)
    user_ref = resolve_user_ref(
        fio,
        exact_index,
        ambiguous,
        users,
        persons=persons,
    )
    user_row = next((user for user in users if user["Ref_Key"] == user_ref), {})
    resolved_fio = user_fio(user_row, persons) or (user_row.get("Description") or fio).strip()
    return user_ref, resolved_fio, users


def user_fio(user: dict[str, Any], persons: dict[str, dict[str, Any]]) -> str:
    person_key = user.get("ФизическоеЛицо_Key")
    if person_key and person_key in persons:
        person = persons[person_key]
        return (person.get("Description") or person.get("ФИО") or "").strip()
    return (user.get("Description") or "").strip()


def build_fio_index(
    users: list[dict[str, Any]],
    persons: dict[str, dict[str, Any]],
) -> tuple[dict[str, str], dict[str, list[str]]]:
    exact: dict[str, str] = {}
    ambiguous: dict[str, list[str]] = {}

    for user in users:
        ref = user.get("Ref_Key")
        if is_empty_key(ref):
            continue
        fio = user_fio(user, persons)
        if not fio:
            continue
        key = normalize_name(fio)
        if key in exact and exact[key] != ref:
            ambiguous.setdefault(key, sorted({exact[key], ref}))
        else:
            exact[key] = ref

    return exact, ambiguous


def resolve_user_ref(
    fio: str,
    exact_index: dict[str, str],
    ambiguous: dict[str, list[str]],
    users: list[dict[str, Any]],
    *,
    persons: dict[str, dict[str, Any]] | None = None,
) -> str:
    key = normalize_name(fio)
    if not key:
        raise ValueError("Пустое ФИО")

    if key in ambiguous:
        raise ValueError(
            f"Неоднозначное ФИО «{fio}»: найдено {len(ambiguous[key])} пользователей"
        )
    if key in exact_index:
        return exact_index[key]

    parts = key.split()
    if len(parts) >= 2:
        surname, name = parts[0], parts[1]
        candidates: list[str] = []
        for user in users:
            ref = user.get("Ref_Key")
            if is_empty_key(ref):
                continue
            user_name = normalize_name(
                user_fio(user, persons or {}),
            )
            user_parts = user_name.split()
            if len(user_parts) >= 2 and user_parts[0] == surname and user_parts[1] == name:
                candidates.append(ref)
        if len(candidates) == 1:
            return candidates[0]
        if len(candidates) > 1:
            raise ValueError(
                f"Неоднозначное ФИО «{fio}»: найдено {len(candidates)} пользователей"
            )

    raise ValueError(f"Пользователь не найден: «{fio}»")
