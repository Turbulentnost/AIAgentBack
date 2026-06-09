"""
Поиск e-mail по ФИО через 1С OData.

Целевой объект: РегистрСведений.CRM_УчетныеЗаписиЭлектроннойПочты.
Если регистр не опубликован в OData (типичный случай), используется эквивалент:
  Catalog_УчетныеЗаписиЭлектроннойПочты (поле ВладелецУчетнойЗаписи_Key).

Дополнительные источники:
  - CRM_ЕмейлДляСинхронизации в Catalog_Пользователи
  - Catalog_Пользователи_КонтактнаяИнформация (тип АдресЭлектроннойПочты)

Примеры:
  python -m app.tools.onec.lookup_email_by_fio "Кербенева Ольга Владимировна"
  python -m app.tools.onec.lookup_email_by_fio "Иванов Иван" "Петров Петр" -o emails.json
"""

from __future__ import annotations

import argparse
import functools
import json
import re
import sys
from typing import Any
from urllib.parse import quote

import requests

from app.integrations.onec_odata import fetch_all
from app.tools.onec.connection import CONFIG, ODataConfig, create_session
from app.tools.onec.lookup_user_ref import (
    build_fio_index,
    load_persons,
    load_users,
    resolve_user_ref,
)

print = functools.partial(print, flush=True)
log = functools.partial(print, flush=True, file=sys.stderr)

EMPTY = "00000000-0000-0000-0000-000000000000"
REGISTER_ENTITY = "InformationRegister_CRM_УчетныеЗаписиЭлектроннойПочты"
MAIL_CATALOG = "Catalog_УчетныеЗаписиЭлектроннойПочты"
USER_CATALOG = "Catalog_Пользователи"
CONTACTS_ENTITY = "Catalog_Пользователи_КонтактнаяИнформация"
EMAIL_TYPE = "АдресЭлектроннойПочты"
EMAIL_PATTERN = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")


def entity_url(base: str, entity: str) -> str:
    return f"{base.rstrip('/')}/{quote(entity)}"


def is_empty_key(value: str | None) -> bool:
    return not value or value == EMPTY


def normalize_email(value: str | None) -> str:
    return (value or "").strip().lower()


def is_valid_email(value: str) -> bool:
    email = normalize_email(value)
    return bool(email) and bool(EMAIL_PATTERN.match(email))


def register_available(session: requests.Session, config: ODataConfig) -> bool:
    url = f"{entity_url(config.url, REGISTER_ENTITY)}?$top=1&$format=json"
    response = session.get(url, timeout=config.timeout)
    return response.ok


def load_register_index(
    session: requests.Session,
    config: ODataConfig,
) -> dict[str, list[dict[str, str]]]:
    """user Ref_Key -> список записей регистра (если опубликован)."""
    index: dict[str, list[dict[str, str]]] = {}
    url = f"{entity_url(config.url, REGISTER_ENTITY)}?$format=json"
    rows = fetch_all(session, url, page=500, timeout=config.timeout)
    for row in rows:
        user_key = row.get("Пользователь_Key") or row.get("User_Key")
        if is_empty_key(user_key):
            continue
        email = (
            row.get("АдресЭлектроннойПочты")
            or row.get("Email")
            or row.get("Представление")
            or ""
        ).strip()
        account_key = row.get("УчетнаяЗапись_Key") or row.get("УчетнаяЗаписьЭлектроннойПочты_Key")
        entry = {
            "email": normalize_email(email),
            "account_key": account_key or "",
            "source": REGISTER_ENTITY,
        }
        index.setdefault(user_key, []).append(entry)
    return index


def load_mail_catalog_index(
    session: requests.Session,
    config: ODataConfig,
) -> dict[str, list[dict[str, str]]]:
    """user Ref_Key -> e-mail из Catalog_УчетныеЗаписиЭлектроннойПочты."""
    index: dict[str, list[dict[str, str]]] = {}
    url = (
        f"{entity_url(config.url, MAIL_CATALOG)}"
        f"?$filter={quote('DeletionMark eq false', safe='')}"
        f"&$select={quote('Ref_Key,Description,АдресЭлектроннойПочты,ВладелецУчетнойЗаписи_Key,CRM_Ответственный_Key', safe=',_')}"
        f"&$format=json"
    )
    for row in fetch_all(session, url, page=500, timeout=config.timeout):
        email = normalize_email(row.get("АдресЭлектроннойПочты") or row.get("Description"))
        if not is_valid_email(email):
            continue
        for user_key in (
            row.get("ВладелецУчетнойЗаписи_Key"),
            row.get("CRM_Ответственный_Key"),
        ):
            if is_empty_key(user_key):
                continue
            index.setdefault(user_key, []).append(
                {
                    "email": email,
                    "account_key": row.get("Ref_Key") or "",
                    "source": f"{MAIL_CATALOG}.ВладелецУчетнойЗаписи_Key",
                }
            )
    return index


def load_user_sync_emails(
    session: requests.Session,
    config: ODataConfig,
) -> dict[str, list[dict[str, str]]]:
    index: dict[str, list[dict[str, str]]] = {}
    url = (
        f"{entity_url(config.url, USER_CATALOG)}"
        f"?$select={quote('Ref_Key,CRM_ЕмейлДляСинхронизации,DeletionMark,Недействителен', safe=',_')}"
        f"&$format=json"
    )
    for row in fetch_all(session, url, page=500, timeout=config.timeout):
        if row.get("DeletionMark") or row.get("Недействителен"):
            continue
        email = normalize_email(row.get("CRM_ЕмейлДляСинхронизации"))
        if not is_valid_email(email):
            continue
        user_key = row.get("Ref_Key")
        if is_empty_key(user_key):
            continue
        index.setdefault(user_key, []).append(
            {
                "email": email,
                "account_key": "",
                "source": f"{USER_CATALOG}.CRM_ЕмейлДляСинхронизации",
            }
        )
    return index


def load_contact_email_index(
    session: requests.Session,
    config: ODataConfig,
) -> dict[str, list[dict[str, str]]]:
    index: dict[str, list[dict[str, str]]] = {}
    contact_filter = f"Тип eq '{EMAIL_TYPE}'"
    url = (
        f"{entity_url(config.url, CONTACTS_ENTITY)}"
        f"?$filter={quote(contact_filter, safe='')}"
        f"&$select={quote('Ref_Key,АдресЭП,Представление,Тип', safe=',_')}"
        f"&$format=json"
    )
    for row in fetch_all(session, url, page=500, timeout=config.timeout):
        email = normalize_email(row.get("АдресЭП") or row.get("Представление"))
        if not is_valid_email(email):
            continue
        user_key = row.get("Ref_Key")
        if is_empty_key(user_key):
            continue
        index.setdefault(user_key, []).append(
            {
                "email": email,
                "account_key": "",
                "source": CONTACTS_ENTITY,
            }
        )
    return index


def dedupe_entries(entries: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[tuple[str, str]] = set()
    result: list[dict[str, str]] = []
    for entry in entries:
        key = (entry["email"], entry["source"])
        if key in seen:
            continue
        seen.add(key)
        result.append(entry)
    return result


def lookup_emails_for_user(
    user_ref: str,
    *,
    register_index: dict[str, list[dict[str, str]]],
    mail_index: dict[str, list[dict[str, str]]],
    sync_index: dict[str, list[dict[str, str]]],
    contact_index: dict[str, list[dict[str, str]]],
    register_published: bool,
) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    if register_published:
        entries.extend(register_index.get(user_ref, []))
    entries.extend(mail_index.get(user_ref, []))
    if not entries:
        entries.extend(sync_index.get(user_ref, []))
        entries.extend(contact_index.get(user_ref, []))
    return dedupe_entries(entries)


def lookup_email_by_fio(
    fio: str,
    *,
    session: requests.Session | None = None,
    config: ODataConfig = CONFIG,
    users: list[dict[str, Any]] | None = None,
    exact_index: dict[str, str] | None = None,
    ambiguous: dict[str, list[str]] | None = None,
    register_index: dict[str, list[dict[str, str]]] | None = None,
    mail_index: dict[str, list[dict[str, str]]] | None = None,
    sync_index: dict[str, list[dict[str, str]]] | None = None,
    contact_index: dict[str, list[dict[str, str]]] | None = None,
    register_published: bool | None = None,
) -> dict[str, Any]:
    session = session or create_session(config)
    persons: dict[str, dict[str, Any]] | None = None

    if users is None or exact_index is None or ambiguous is None:
        users = load_users(session, config=config)
        person_keys = {
            user.get("ФизическоеЛицо_Key")
            for user in users
            if user.get("ФизическоеЛицо_Key") and not is_empty_key(user.get("ФизическоеЛицо_Key"))
        }
        persons = load_persons(session, person_keys, config=config)
        exact_index, ambiguous = build_fio_index(users, persons)

    user_ref = resolve_user_ref(
        fio,
        exact_index,
        ambiguous,
        users,
        persons=persons,
    )
    user_row = next((user for user in users if user["Ref_Key"] == user_ref), {})
    resolved_fio = (user_row.get("Description") or fio).strip()

    if register_published is None:
        register_published = register_available(session, config)
    if register_index is None:
        register_index = load_register_index(session, config) if register_published else {}
    if mail_index is None:
        mail_index = load_mail_catalog_index(session, config)
    if sync_index is None:
        sync_index = load_user_sync_emails(session, config)
    if contact_index is None:
        contact_index = load_contact_email_index(session, config)

    emails = lookup_emails_for_user(
        user_ref,
        register_index=register_index,
        mail_index=mail_index,
        sync_index=sync_index,
        contact_index=contact_index,
        register_published=register_published,
    )

    if not emails:
        raise LookupError(
            f"E-mail не найден для «{resolved_fio}» ({user_ref}). "
            f"Регистр {REGISTER_ENTITY} "
            f"{'опубликован, но пуст' if register_published else 'не опубликован в OData'}."
        )

    return {
        "fio_query": fio.strip(),
        "fio": resolved_fio,
        "user_ref": user_ref,
        "register_published": register_published,
        "emails": emails,
    }


def format_result(result: dict[str, Any]) -> str:
    lines = [
        f"ФИО: {result['fio']}",
        f"Ref_Key: {result['user_ref']}",
    ]
    if not result["register_published"]:
        lines.append(
            f"Примечание: {REGISTER_ENTITY} не опубликован в OData; "
            f"использован {MAIL_CATALOG}."
        )
    lines.append("E-mail:")
    for item in result["emails"]:
        lines.append(f"  - {item['email']}  ({item['source']})")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Поиск e-mail по ФИО (1С OData).")
    parser.add_argument(
        "fio",
        nargs="+",
        help="ФИО пользователя (можно несколько)",
    )
    parser.add_argument(
        "-o",
        "--output",
        help="Сохранить результат в JSON",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    args = build_parser().parse_args(argv)
    session = create_session(CONFIG)

    log("Загрузка справочников и регистров ...")
    users = load_users(session, config=CONFIG)
    person_keys = {
        user.get("ФизическоеЛицо_Key")
        for user in users
        if user.get("ФизическоеЛицо_Key") and not is_empty_key(user.get("ФизическоеЛицо_Key"))
    }
    persons = load_persons(session, person_keys, config=CONFIG)
    exact_index, ambiguous = build_fio_index(users, persons)

    register_published = register_available(session, CONFIG)
    register_index = load_register_index(session, CONFIG) if register_published else {}
    mail_index = load_mail_catalog_index(session, CONFIG)
    sync_index = load_user_sync_emails(session, CONFIG)
    contact_index = load_contact_email_index(session, CONFIG)
    log(
        f"  Пользователей: {len(users)}; "
        f"учётных записей почты: {sum(len(v) for v in mail_index.values())}; "
        f"регистр CRM: {'да' if register_published else 'нет'}"
    )

    results: list[dict[str, Any]] = []
    exit_code = 0
    for fio in args.fio:
        try:
            result = lookup_email_by_fio(
                fio,
                session=session,
                users=users,
                exact_index=exact_index,
                ambiguous=ambiguous,
                register_index=register_index,
                mail_index=mail_index,
                sync_index=sync_index,
                contact_index=contact_index,
                register_published=register_published,
            )
            results.append(result)
            print(format_result(result))
            print()
        except (LookupError, ValueError) as error:
            log(f"Ошибка для «{fio}»: {error}")
            exit_code = 1

    if args.output and results:
        payload = {
            "register_entity": REGISTER_ENTITY,
            "register_published": register_published,
            "results": results,
        }
        with open(args.output, "w", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False, indent=2)
        log(f"Сохранено: {args.output}")

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
