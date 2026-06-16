"""
Поиск e-mail по ФИО через 1С OData.

Целевой объект: РегистрСведений.CRM_УчетныеЗаписиЭлектроннойПочты.
Если регистр не опубликован в OData (типичный случай), используется эквивалент:
  Catalog_УчетныеЗаписиЭлектроннойПочты (поле ВладелецУчетнойЗаписи_Key).

Дополнительные источники:
  - CRM_ЕмейлДляСинхронизации в Catalog_Пользователи
  - Catalog_Пользователи_КонтактнаяИнформация (тип АдресЭлектроннойПочты)
  - InformationRegister_CRM_УчетныеЗаписиЭлектроннойПочты → Catalog_УчетныеЗаписиЭлектроннойПочты
  - Catalog_СтроковыеКонтактыВзаимодействий (строка «ФИО <email@domain>»)
  - Exchange GAL / OWA (EWS ResolveNames) — каталог mail.turbo-don.ru

Возвращаются только корпоративные адреса (@ONEC_CORPORATE_EMAIL_DOMAIN, по умолчанию turbo-don.ru).

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

from app.core.config import settings
from app.integrations.onec_odata import fetch_all
from app.tools.onec.connection import CONFIG, ODataConfig, create_session
from app.tools.onec.exchange_gal_lookup import EXCHANGE_GAL_SOURCE, load_exchange_gal_emails_for_fio
from app.tools.onec.lookup_user_ref import normalize_name, resolve_user_by_fio

print = functools.partial(print, flush=True)
log = functools.partial(print, flush=True, file=sys.stderr)

EMPTY = "00000000-0000-0000-0000-000000000000"
REGISTER_ENTITY = "InformationRegister_CRM_УчетныеЗаписиЭлектроннойПочты"
MAIL_CATALOG = "Catalog_УчетныеЗаписиЭлектроннойПочты"
USER_CATALOG = "Catalog_Пользователи"
PERSON_CATALOG = "Catalog_ФизическиеЛица"
CONTACTS_ENTITY = "Catalog_Пользователи_КонтактнаяИнформация"
PERSON_CONTACTS_ENTITY = "Catalog_ФизическиеЛица_КонтактнаяИнформация"
STRING_CONTACTS_CATALOG = "Catalog_СтроковыеКонтактыВзаимодействий"
EMAIL_TYPE = "АдресЭлектроннойПочты"
EMAIL_PATTERN = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")
DESCRIPTION_EMAIL_PATTERN = re.compile(
    r"<([A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,})>"
)
DESCRIPTION_EMAIL_IN_PARENS_PATTERN = re.compile(
    r"\(([A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,})\)"
)


def corporate_email_domain() -> str:
    return (settings.ONEC_CORPORATE_EMAIL_DOMAIN or "turbo-don.ru").strip().lower().lstrip("@")


def is_corporate_email(email: str) -> bool:
    normalized = normalize_email(email)
    if not normalized:
        return False
    return normalized.endswith(f"@{corporate_email_domain()}")


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


def fetch_mail_account_email(
    session: requests.Session,
    config: ODataConfig,
    account_key: str,
) -> str:
    if is_empty_key(account_key):
        return ""
    url = f"{entity_url(config.url, MAIL_CATALOG)}(guid'{account_key}')?$format=json"
    response = session.get(url, timeout=config.timeout)
    if not response.ok:
        return ""
    row = response.json()
    return normalize_email(row.get("АдресЭлектроннойПочты") or row.get("Description"))


def load_crm_register_mail_index(
    session: requests.Session,
    config: ODataConfig,
) -> dict[str, list[dict[str, Any]]]:
    """user Ref_Key -> e-mail через регистр CRM и Catalog_УчетныеЗаписиЭлектроннойПочты."""
    index: dict[str, list[dict[str, Any]]] = {}
    url = f"{entity_url(config.url, REGISTER_ENTITY)}?$format=json"
    for row in odata_get_rows(session, url, timeout=config.timeout):
        user_key = row.get("Пользователь") or row.get("Пользователь_Key") or row.get("User_Key")
        account_key = row.get("УчетнаяЗапись_Key") or row.get("УчетнаяЗаписьЭлектроннойПочты_Key")
        if is_empty_key(user_key) or is_empty_key(account_key):
            continue
        email = fetch_mail_account_email(session, config, account_key)
        if not is_valid_email(email):
            continue
        index.setdefault(user_key, []).append(
            {
                "email": email,
                "account_key": account_key,
                "source": REGISTER_ENTITY,
                "primary": bool(row.get("Основная")),
            }
        )
    return index


def load_user_person_key(
    session: requests.Session,
    config: ODataConfig,
    user_ref: str,
) -> str:
    url = (
        f"{entity_url(config.url, USER_CATALOG)}(guid'{user_ref}')"
        f"?$select={quote('Ref_Key,ФизическоеЛицо_Key,DeletionMark,Недействителен', safe=',_')}"
        f"&$format=json"
    )
    response = session.get(url, timeout=config.timeout)
    if not response.ok:
        return ""
    row = response.json()
    if row.get("DeletionMark") or row.get("Недействителен"):
        return ""
    return row.get("ФизическоеЛицо_Key") or ""


def load_contact_emails_for_ref(
    session: requests.Session,
    config: ODataConfig,
    owner_ref: str,
    *,
    entity: str,
) -> list[dict[str, str]]:
    filter_expr = f"Ref_Key eq guid'{owner_ref}' and Тип eq '{EMAIL_TYPE}'"
    url = (
        f"{entity_url(config.url, entity)}"
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
                "source": entity,
            }
        )
    return entries


def load_register_emails_for_user(
    session: requests.Session,
    config: ODataConfig,
    user_ref: str,
    *,
    register_index: dict[str, list[dict[str, Any]]] | None = None,
) -> list[dict[str, str]]:
    if register_index is not None:
        return [
            {
                "email": item["email"],
                "account_key": item.get("account_key", ""),
                "source": item.get("source", REGISTER_ENTITY),
            }
            for item in register_index.get(user_ref, [])
            if item.get("email")
        ]

    entries: list[dict[str, str]] = []
    url = f"{entity_url(config.url, REGISTER_ENTITY)}?$format=json"
    for row in odata_get_rows(session, url, timeout=config.timeout):
        user_key = row.get("Пользователь") or row.get("Пользователь_Key")
        if user_key != user_ref:
            continue
        account_key = row.get("УчетнаяЗапись_Key") or row.get("УчетнаяЗаписьЭлектроннойПочты_Key")
        email = fetch_mail_account_email(session, config, account_key)
        if not is_valid_email(email):
            continue
        entries.append(
            {
                "email": email,
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
    return load_contact_emails_for_ref(
        session,
        config,
        user_ref,
        entity=CONTACTS_ENTITY,
    )


def load_person_contact_emails(
    session: requests.Session,
    config: ODataConfig,
    person_ref: str,
) -> list[dict[str, str]]:
    if is_empty_key(person_ref):
        return []
    return load_contact_emails_for_ref(
        session,
        config,
        person_ref,
        entity=PERSON_CONTACTS_ENTITY,
    )


def description_matches_fio(fio: str, description: str) -> bool:
    name_part = description.split("<", 1)[0].split("(", 1)[0].strip()
    if not name_part:
        return False
    return normalize_name(name_part) == normalize_name(fio)


def extract_emails_from_description(description: str) -> list[str]:
    emails: list[str] = []
    for pattern in (DESCRIPTION_EMAIL_PATTERN, DESCRIPTION_EMAIL_IN_PARENS_PATTERN):
        emails.extend(match.group(1) for match in pattern.finditer(description))
    if not emails and is_valid_email(description.strip()):
        emails.append(description.strip())
    return emails


def load_string_contact_emails_for_fio(
    session: requests.Session,
    config: ODataConfig,
    fio: str,
) -> list[dict[str, str]]:
    """E-mail из Catalog_СтроковыеКонтактыВзаимодействий: «ФИО <email@domain>»."""
    parts = normalize_name(fio).split()
    if not parts:
        return []

    surname = odata_escape(parts[0])
    filter_expr = f"DeletionMark eq false and substringof('{surname}', Description) eq true"
    url = (
        f"{entity_url(config.url, STRING_CONTACTS_CATALOG)}"
        f"?$filter={quote(filter_expr, safe='')}"
        f"&$select={quote('Ref_Key,Description,DeletionMark', safe=',_')}"
        f"&$format=json"
    )

    entries: list[dict[str, str]] = []
    for row in odata_get_rows(session, url, timeout=config.timeout):
        description = (row.get("Description") or "").strip()
        if not description or not description_matches_fio(fio, description):
            continue
        for raw_email in extract_emails_from_description(description):
            email = normalize_email(raw_email)
            if not is_valid_email(email):
                continue
            entries.append(
                {
                    "email": email,
                    "account_key": row.get("Ref_Key") or "",
                    "source": STRING_CONTACTS_CATALOG,
                }
            )
    return entries


def rank_corporate_email_entries(entries: list[dict[str, str]]) -> list[dict[str, str]]:
    """Оставляет только @turbo-don.ru (или ONEC_CORPORATE_EMAIL_DOMAIN) и сортирует по источнику."""
    source_priority = {
        EXCHANGE_GAL_SOURCE: 0,
        REGISTER_ENTITY: 1,
        f"{MAIL_CATALOG}.ВладелецУчетнойЗаписи_Key": 2,
        STRING_CONTACTS_CATALOG: 3,
        f"{USER_CATALOG}.CRM_ЕмейлДляСинхронизации": 4,
        CONTACTS_ENTITY: 5,
    }

    corporate = [entry for entry in dedupe_entries(entries) if is_corporate_email(entry.get("email", ""))]

    def sort_key(entry: dict[str, str]) -> tuple[int, str]:
        source = source_priority.get(entry.get("source", ""), 5)
        return (source, entry.get("email", ""))

    ranked = sorted(corporate, key=sort_key)
    seen_emails: set[str] = set()
    unique: list[dict[str, str]] = []
    for entry in ranked:
        email = entry.get("email", "")
        if email in seen_emails:
            continue
        seen_emails.add(email)
        unique.append(entry)
    return unique


def lookup_emails_for_user_ref(
    session: requests.Session,
    config: ODataConfig,
    user_ref: str,
    *,
    resolved_fio: str = "",
    register_published: bool | None = None,
    register_index: dict[str, list[dict[str, Any]]] | None = None,
    exchange_account: Any | None = None,
) -> list[dict[str, str]]:
    if register_published is None:
        register_published = register_available(session, config)

    entries: list[dict[str, str]] = []
    if register_published:
        entries.extend(
            load_register_emails_for_user(
                session,
                config,
                user_ref,
                register_index=register_index,
            )
        )
    entries.extend(load_mail_emails_for_user(session, config, user_ref))
    entries.extend(load_sync_email_for_user(session, config, user_ref))
    entries.extend(load_contact_emails_for_user(session, config, user_ref))
    if resolved_fio:
        entries.extend(load_string_contact_emails_for_fio(session, config, resolved_fio))
        entries.extend(
            load_exchange_gal_emails_for_fio(
                resolved_fio,
                account=exchange_account,
            )
        )
    return rank_corporate_email_entries([entry for entry in entries if entry.get("email")])


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
    register_index: dict[str, list[dict[str, Any]]] | None = None,
    exchange_account: Any | None = None,
) -> dict[str, Any]:
    session = session or create_session(config)
    user_ref, resolved_fio, _users = resolve_user_by_fio(session, fio, config=config)

    if register_published is None:
        register_published = register_available(session, config)

    emails = lookup_emails_for_user_ref(
        session,
        config,
        user_ref,
        resolved_fio=resolved_fio,
        register_published=register_published,
        register_index=register_index,
        exchange_account=exchange_account,
    )

    if not emails:
        domain = corporate_email_domain()
        raise LookupError(
            f"Корпоративный e-mail @{domain} не найден для «{resolved_fio}» ({user_ref}). "
            "Проверьте 1С OData и каталог пользователей Exchange (OWA)."
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
    register_index = load_crm_register_mail_index(session, config) if register_published else {}

    exchange_account = None
    try:
        from app.tools.Outlook.send_meeting_invite import connect_account, load_config

        outlook_config = load_config()
        if outlook_config.email and outlook_config.password:
            exchange_account = connect_account(outlook_config)
    except (ValueError, OSError):
        exchange_account = None

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
                    register_index=register_index,
                    exchange_account=exchange_account,
                )
            )
        except (LookupError, ValueError) as error:
            errors.append({"fio": query, "error": str(error)})

    if not results and errors:
        raise ValueError(errors[0]["error"])

    return {
        "register_entity": REGISTER_ENTITY,
        "register_published": register_published,
        "corporate_email_domain": corporate_email_domain(),
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
