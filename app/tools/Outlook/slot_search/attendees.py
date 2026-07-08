from __future__ import annotations

from typing import Any

from .constants import RESOURCE_CALENDAR_PREFIXES


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

def _is_resource_calendar_email(email: str) -> bool:
    normalized = email.strip().lower()
    return any(normalized.startswith(prefix) for prefix in RESOURCE_CALENDAR_PREFIXES)

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

