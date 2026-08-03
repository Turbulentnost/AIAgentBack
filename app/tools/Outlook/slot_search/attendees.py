from __future__ import annotations

import re
from typing import Any

from .constants import RESOURCE_CALENDAR_PREFIXES

_CYRILLIC_RE = re.compile(r"[\u0400-\u04FF]")
_DEPARTMENT_MAILBOX_RE = re.compile(r"[_\d]")


def normalize_calendar_email(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip().lower()
        return text if "@" in text else None
    mailbox = getattr(value, "mailbox", None)
    if mailbox is not None:
        address = getattr(mailbox, "email_address", None)
        if isinstance(address, str) and "@" in address:
            return address.strip().lower()
    address = getattr(value, "email_address", None)
    if isinstance(address, str) and "@" in address:
        return address.strip().lower()
    return None

def calendar_attendee_display_name(entry: Any) -> str | None:
    mailbox = getattr(entry, "mailbox", None)
    if mailbox is not None:
        name = (getattr(mailbox, "name", "") or "").strip()
        if name and "@" not in name and _CYRILLIC_RE.search(name):
            return name
    return None

def calendar_item_attendee_display_names(item: Any) -> list[str]:
    """Отображаемые имена участников встречи из EWS CalendarItem."""
    names: list[str] = []
    for attr in ("required_attendees", "optional_attendees"):
        for entry in getattr(item, attr, None) or []:
            name = calendar_attendee_display_name(entry)
            if name:
                names.append(name)
    organizer = getattr(item, "organizer", None)
    if organizer is not None:
        organizer_name = calendar_attendee_display_name(organizer)
        if organizer_name:
            names.append(organizer_name)
    return list(dict.fromkeys(names))

def calendar_item_attendee_emails(item: Any) -> list[str]:
    """E-mail участников встречи из EWS CalendarItem."""
    emails: list[str] = []
    for attr in ("required_attendees", "optional_attendees"):
        for entry in getattr(item, attr, None) or []:
            normalized = normalize_calendar_email(entry)
            if normalized:
                emails.append(normalized)
    organizer = normalize_calendar_email(getattr(item, "organizer", None))
    if organizer:
        emails.append(organizer)
    return list(dict.fromkeys(emails))

def calendar_item_attendee_entries(item: Any) -> list[tuple[str | None, str]]:
    """Пары (отображаемое имя, e-mail) участников CalendarItem."""
    entries: list[tuple[str | None, str]] = []
    for attr in ("required_attendees", "optional_attendees"):
        for entry in getattr(item, attr, None) or []:
            email = normalize_calendar_email(entry)
            if not email:
                continue
            entries.append((calendar_attendee_display_name(entry), email))
    organizer = getattr(item, "organizer", None)
    organizer_email = normalize_calendar_email(organizer)
    if organizer_email:
        entries.append((calendar_attendee_display_name(organizer), organizer_email))

    deduped: list[tuple[str | None, str]] = []
    seen: set[str] = set()
    for name, email in entries:
        key = email.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append((name, email))
    return deduped

def _is_resource_calendar_email(email: str) -> bool:
    normalized = email.strip().lower()
    return any(normalized.startswith(prefix) for prefix in RESOURCE_CALENDAR_PREFIXES)

def is_department_mailbox_email(email: str) -> bool:
    """Служебные/цеховые ящики без персонального ФИО."""
    normalized = email.strip().lower()
    if not normalized or "@" not in normalized:
        return True
    if _is_resource_calendar_email(normalized):
        return True
    local = normalized.split("@", 1)[0]
    if local in {"zavgar", "gk_secretar"}:
        return True
    if "." in local and not _DEPARTMENT_MAILBOX_RE.search(local):
        return False
    return bool(_DEPARTMENT_MAILBOX_RE.search(local))

def _human_attendees_for_reschedule_hint(attendee_emails: list[str]) -> list[str]:
    """Участники для групповой проверки альтернативы; без комнат/ресурсных календарей."""
    return [
        email
        for email in attendee_emails
        if email and not _is_resource_calendar_email(email)
    ]

def _human_calendar_attendee_emails(item: Any) -> list[str]:
    return [
        email
        for email in calendar_item_attendee_emails(item)
        if email and not _is_resource_calendar_email(email)
    ]


def participant_involved_in_calendar_item(
    item: Any,
    *,
    attendee_email: str,
    attendee_fio: str | None = None,
) -> bool:
    """Участник указан в событии общего календаря (email, организатор или ФИО)."""
    normalized_email = attendee_email.strip().lower()
    if not normalized_email:
        return False

    involved_emails = {email.lower() for email in calendar_item_attendee_emails(item)}
    if normalized_email in involved_emails:
        return True

    organizer = normalize_calendar_email(getattr(item, "organizer", None))
    if organizer == normalized_email:
        return True

    fio = (attendee_fio or "").strip()
    if not fio:
        return False

    surname = fio.split()[0].casefold()
    if not surname:
        return False

    for display_name in calendar_item_attendee_display_names(item):
        if surname in display_name.casefold():
            return True
    return False

