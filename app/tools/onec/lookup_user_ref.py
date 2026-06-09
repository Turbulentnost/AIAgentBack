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


def is_empty_key(value: str | None) -> bool:
    return not value or value == EMPTY


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


def load_persons(
    session: requests.Session,
    person_keys: set[str],
    *,
    config: ODataConfig = CONFIG,
) -> dict[str, dict[str, Any]]:
    if not person_keys:
        return {}
    url = (
        f"{entity_url(config.url, PERSON_CATALOG)}"
        f"?$filter={quote('DeletionMark eq false', safe='')}"
        f"&$format=json"
    )
    rows = fetch_all(session, url, page=500, timeout=config.timeout)
    return {
        row["Ref_Key"]: row
        for row in rows
        if row.get("Ref_Key") in person_keys
    }


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
