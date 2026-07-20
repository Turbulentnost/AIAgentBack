"""Поиск корпоративного e-mail в глобальной адресной книге Exchange (EWS ResolveNames)."""
from __future__ import annotations

import re
from typing import Any

from exchangelib import Account

from app.core.config import settings
from app.tools.Outlook.outlook_config import OutlookConfig
from app.tools.Outlook.send_meeting_invite import connect_account, load_config
from app.tools.onec.lookup_user_ref import normalize_name

EXCHANGE_GAL_SOURCE = "Exchange-GAL(EWS ResolveNames)"
EMAIL_PATTERN = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")


def corporate_email_domain() -> str:
    return (settings.ONEC_CORPORATE_EMAIL_DOMAIN or "turbo-don.ru").strip().lower().lstrip("@")


def normalize_email(value: str | None) -> str:
    return (value or "").strip().lower()


def is_valid_email(value: str) -> bool:
    email = normalize_email(value)
    return bool(email) and bool(EMAIL_PATTERN.match(email))


def is_corporate_email(email: str) -> bool:
    normalized = normalize_email(email)
    return bool(normalized) and normalized.endswith(f"@{corporate_email_domain()}")


def _gal_queries(fio: str) -> list[str]:
    query = fio.strip()
    if not query:
        return []
    queries = [query]
    parts = query.split()
    if len(parts) >= 2:
        short = f"{parts[0]} {parts[1]}"
        if short not in queries:
            queries.append(short)
    return queries


def _mailbox_name(mailbox: Any) -> str:
    return (getattr(mailbox, "name", "") or "").strip()


def _mailbox_email(mailbox: Any) -> str:
    return normalize_email(getattr(mailbox, "email_address", "") or "")


def _name_matches_fio(fio: str, resolved_name: str) -> bool:
    target = normalize_name(fio)
    name = normalize_name(resolved_name)
    if not target or not name:
        return False
    if name == target:
        return True
    target_parts = target.split()
    name_parts = name.split()
    return len(target_parts) >= 2 and len(name_parts) >= 2 and (
        name_parts[0] == target_parts[0] and name_parts[1] == target_parts[1]
    )


def load_exchange_gal_emails_for_fio(
    fio: str,
    *,
    account: Account | None = None,
    config: OutlookConfig | None = None,
) -> list[dict[str, str]]:
    """Ищет SMTP в OWA/Exchange GAL через EWS ResolveNames."""
    config = config or load_config()
    if not config.email or not config.password:
        return []

    try:
        account = account or connect_account(config)
    except (ValueError, OSError):
        return []

    entries: list[dict[str, str]] = []
    seen_emails: set[str] = set()
    for query in _gal_queries(fio):
        try:
            matches = account.protocol.resolve_names([query], return_full_contact_data=True)
        except Exception:
            continue
        for item in matches or []:
            mailbox = item[0] if isinstance(item, tuple) else item
            resolved_name = _mailbox_name(mailbox)
            email = _mailbox_email(mailbox)
            if not is_valid_email(email) or not is_corporate_email(email):
                continue
            if not _name_matches_fio(fio, resolved_name):
                continue
            if email in seen_emails:
                continue
            seen_emails.add(email)
            entries.append(
                {
                    "email": email,
                    "account_key": "",
                    "source": EXCHANGE_GAL_SOURCE,
                }
            )
    return entries
