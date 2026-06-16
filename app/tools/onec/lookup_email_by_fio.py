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
from app.tools.onec.lookup_user_ref import resolve_user_by_fio

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


def odata_escape(value: str) -> str:
    return value.replace("'", "''")


def odata_get_rows(
    session: requests.Session,
    url: str,
    *,
    timeout: int,
) -> list[dict[str, Any]]:
    response = session.get(url, timeout=timeout)
    if not response.ok:
        return []
    return response.json().get("value") or []


def load_register_emails_for_user(
    session: requests.Session,
    config: ODataConfig,
    user_ref: str,
) -> list[dict[str, str]]:
    filter_expr = f"Пользователь_Key eq guid'{user_ref}'"
    url = (
        f"{entity_url(config.url, REGISTER_ENTITY)}"
        f"?$filter={quote(filter_expr, safe='')}"
        f"&$format=json"
    )
    entries: list[dict[str, str]] = []
    for row in odata_get_rows(session, url, timeout=config.timeout):
        email = (
            row.get("АдресЭлектроннойПочты")
            or row.get("Email")
            or row.get("Представление")
            or ""
        ).strip()
        account_key = row.get("УчетнаяЗапись_Key") or row.get("УчетнаяЗаписьЭлектроннойПочты_Key")
        entries.append(
            {
                "email": normalize_email(email),
                "account_key": account_key or "",
                "source": REGISTER_ENTITY,
            }
        )
    return entries


def load_mail_emails_for_user(
    session: requests.Session,
    config: ODataConfig,
    user_ref: str,
) -> list[dict[str, str]]:
    filter_expr = (
        f"DeletionMark eq false and "
        f"(ВладелецУчетнойЗаписи_Key eq guid'{user_ref}' or "
        f"CRM_Ответственный_Key eq guid'{user_ref}')"
    )
    url = (
        f"{entity_url(config.url, MAIL_CATALOG)}"
        f"?$filter={quote(filter_expr, safe='')}"
        f"&$select={quote('Ref_Key,Description,АдресЭлектроннойПочты,ВладелецУчетнойЗаписи_Key,CRM_Ответственный_Key', safe=',_')}"
        f"&$format=json"
    )
    entries: list[dict[str, str]] = []
    for row in odata_get_rows(session, url, timeout=config.timeout):
        email = normalize_email(row.get("АдресЭлектроннойПочты") or row.get("Description"))
        if not is_valid_email(email):
            continue
        entries.append(
            {
                "email": email,
                "account_key": row.get("Ref_Key") or "",
                "source": f"{MAIL_CATALOG}.ВладелецУчетнойЗаписи_Key",
            }
        )
    return entries


def load_sync_email_for_user(
    session: requests.Session,
    config: ODataConfig,
    user_ref: str,
) -> list[dict[str, str]]:
    url = (
        f"{entity_url(config.url, USER_CATALOG)}(guid'{user_ref}')"
        f"?$select={quote('Ref_Key,CRM_ЕмейлДляСинхронизации,DeletionMark,Недействителен', safe=',_')}"
        f"&$format=json"
    )
    response = session.get(url, timeout=config.timeout)
    if not response.ok:
        return []
    row = response.json()
    if row.get("DeletionMark") or row.get("Недействителен"):
        return []
    email = normalize_email(row.get("CRM_ЕмейлДляСинхронизации"))
    if not is_valid_email(email):
        return []
    return [
        {
            "email": email,
            "account_key": "",
            "source": f"{USER_CATALOG}.CRM_ЕмейлДляСинхронизации",
        }
    ]


def load_contact_emails_for_user(
    session: requests.Session,
    config: ODataConfig,
    user_ref: str,
) -> list[dict[str, str]]:
    filter_expr = f"Ref_Key eq guid'{user_ref}' and Тип eq '{EMAIL_TYPE}'"
    url = (
        f"{entity_url(config.url, CONTACTS_ENTITY)}"
        f"?$filter={quote(filter_expr, safe='')}"
        f"&$select={quote('Ref_Key,АдресЭП,Представление,Тип', safe=',_')}"
        f"&$format=json"
    )
    entries: list[dict[str, str]] = []
    for row in odata_get_rows(session, url, timeout=config.timeout):
        email = normalize_email(row.get("АдресЭП") or row.get("Представление"))
        if not is_valid_email(email):
            continue
        entries.append(
            {
                "email": email,
                "account_key": "",
                "source": CONTACTS_ENTITY,
            }
        )
    return entries


def lookup_emails_for_user_ref(
    session: requests.Session,
    config: ODataConfig,
    user_ref: str,
    *,
    register_published: bool | None = None,
) -> list[dict[str, str]]:
    if register_published is None:
        register_published = register_available(session, config)

    entries: list[dict[str, str]] = []
    if register_published:
        entries.extend(load_register_emails_for_user(session, config, user_ref))
    entries.extend(load_mail_emails_for_user(session, config, user_ref))
    if not entries:
        entries.extend(load_sync_email_for_user(session, config, user_ref))
        entries.extend(load_contact_emails_for_user(session, config, user_ref))
    return dedupe_entries([entry for entry in entries if entry["email"]])


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
    register_published: bool | None = None,
) -> dict[str, Any]:
    session = session or create_session(config)
    user_ref, resolved_fio, _users = resolve_user_by_fio(session, fio, config=config)

    if register_published is None:
        register_published = register_available(session, config)

    emails = lookup_emails_for_user_ref(
        session,
        config,
        user_ref,
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


def dispatch_lookup_emails_by_fio(
    fio_list: list[str],
    *,
    config: ODataConfig | None = None,
) -> dict[str, Any]:
    """Ищет e-mail по списку ФИО и возвращает JSON для API/агента."""
    config = config or CONFIG
    session = create_session(config)
    register_published = register_available(session, config)

    results: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for fio in fio_list:
        query = fio.strip()
        if not query:
            continue
        try:
            results.append(
                lookup_email_by_fio(
                    query,
                    session=session,
                    config=config,
                    register_published=register_published,
                )
            )
        except (LookupError, ValueError) as error:
            errors.append({"fio": query, "error": str(error)})

    if not results and errors:
        raise ValueError(errors[0]["error"])

    return {
        "register_entity": REGISTER_ENTITY,
        "register_published": register_published,
        "results": results,
        "errors": errors,
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

    try:
        payload = dispatch_lookup_emails_by_fio(args.fio)
    except ValueError as error:
        log(f"Ошибка: {error}")
        return 1

    exit_code = 1 if payload["errors"] else 0
    for result in payload["results"]:
        print(format_result(result))
        print()
    for item in payload["errors"]:
        log(f"Ошибка для «{item['fio']}»: {item['error']}")
        exit_code = 1

    if args.output and payload["results"]:
        with open(args.output, "w", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False, indent=2)
        log(f"Сохранено: {args.output}")

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
