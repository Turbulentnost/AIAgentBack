"""Тексты и рассылка уведомлений при изменении состава совещания."""

from __future__ import annotations

from typing import Any, Literal

from exchangelib import Account, Message
from exchangelib.items import SEND_ONLY_TO_CHANGED
from exchangelib.properties import HTMLBody, Mailbox

from app.tools.Outlook.meeting_rooms import load_rooms
from app.tools.Outlook.outlook_html_body import plain_text_to_html
from app.tools.Outlook.outlook_config import OutlookConfig
from app.tools.Outlook.slot_search.attendees import (
    calendar_attendee_display_name,
    normalize_calendar_email,
    _is_resource_calendar_email,
)

AttendeeField = Literal["required_attendees", "optional_attendees"]
ATTENDEE_FIELDS: tuple[AttendeeField, ...] = ("required_attendees", "optional_attendees")
INVITE_AGENT_FOOTER = "Совещание запланировано ИИ-агентом по планированию совещаний"

AttendeePair = tuple[str, str]


def attendee_email(attendee: Any) -> str | None:
    mailbox = getattr(attendee, "mailbox", None)
    if mailbox is None:
        return None
    address = getattr(mailbox, "email_address", None)
    return str(address).strip() if address else None


def organizer_email(item: Any) -> str | None:
    organizer_obj = getattr(item, "organizer", None)
    if organizer_obj is None:
        return None
    address = getattr(organizer_obj, "email_address", None) or str(organizer_obj)
    return str(address).strip().lower() if address else None


def attendee_line(fio: str, email: str) -> str:
    return f"{fio.strip()} <{email.strip()}>"


def attendee_lines(pairs: list[AttendeePair]) -> list[str]:
    return [attendee_line(fio, email) for fio, email in pairs]


def _room_name_by_email() -> dict[str, str]:
    return {
        room["email"].strip().lower(): room["name"].strip()
        for room in load_rooms()
        if room.get("email")
    }


def _display_name_from_gal(account: Account, email: str) -> str | None:
    try:
        matches = account.protocol.resolve_names([email], return_full_contact_data=True)
    except Exception:
        return None
    for item in matches or []:
        mailbox = item[0] if isinstance(item, tuple) else item
        resolved_email = (getattr(mailbox, "email_address", "") or "").strip().lower()
        if resolved_email != email.strip().lower():
            continue
        name = (getattr(mailbox, "name", "") or "").strip()
        if name and "@" not in name:
            return name
    return None


def resolve_attendee_pair(
    email: str,
    *,
    item: Any,
    account: Account | None = None,
    room_names: dict[str, str] | None = None,
) -> AttendeePair:
    normalized = email.strip()
    key = normalized.lower()
    rooms = room_names if room_names is not None else _room_name_by_email()

    for field in ATTENDEE_FIELDS:
        for attendee in getattr(item, field, None) or []:
            address = attendee_email(attendee)
            if not address or address.lower() != key:
                continue
            name = calendar_attendee_display_name(attendee)
            if name:
                return name, normalized

    room_name = rooms.get(key)
    if room_name:
        return room_name, normalized

    if account is not None:
        gal_name = _display_name_from_gal(account, normalized)
        if gal_name:
            return gal_name, normalized

    return normalized, normalized


def resolve_attendee_pairs(
    emails: list[str],
    *,
    item: Any,
    account: Account | None = None,
) -> list[AttendeePair]:
    room_names = _room_name_by_email()
    return [
        resolve_attendee_pair(email, item=item, account=account, room_names=room_names)
        for email in emails
    ]


def is_notification_recipient(email: str) -> bool:
    normalized = normalize_calendar_email(email)
    if not normalized:
        return False
    if _is_resource_calendar_email(normalized):
        return False
    return normalized not in _room_name_by_email()


def build_existing_attendees_notification_body(
    *,
    added_pairs: list[AttendeePair],
    removed_pairs: list[AttendeePair],
    roster_pairs: list[AttendeePair],
    extra_message: str = "",
    footer: str = INVITE_AGENT_FOOTER,
) -> str:
    sections = ["Произошло обновление состава участников", ""]
    if added_pairs:
        sections.append("Новые участники:")
        sections.extend(attendee_lines(added_pairs))
        sections.append("")
    if removed_pairs:
        sections.append("Исключённые участники:")
        sections.extend(attendee_lines(removed_pairs))
        sections.append("")
    sections.append("Обновленный состав:")
    sections.extend(attendee_lines(roster_pairs))
    if extra_message.strip():
        sections.extend(["", extra_message.strip()])
    if footer.strip():
        sections.extend(["", footer.strip()])
    return "\n".join(sections)


def build_new_attendees_notification_body(
    *,
    subject: str,
    roster_pairs: list[AttendeePair],
    extra_message: str = "",
    footer: str = INVITE_AGENT_FOOTER,
) -> str:
    sections = [
        f'Вы были добавлены участником на совещание по теме "{subject.strip()}"',
        "",
        "Участники:",
    ]
    sections.extend(attendee_lines(roster_pairs))
    if extra_message.strip():
        sections.extend(["", extra_message.strip()])
    if footer.strip():
        sections.extend(["", footer.strip()])
    return "\n".join(sections)


def build_removed_attendees_notification_body(
    *,
    subject: str,
    extra_message: str = "",
    footer: str = INVITE_AGENT_FOOTER,
) -> str:
    sections = [
        f'Вы были исключены из участников совещания по теме "{subject.strip()}"',
    ]
    if extra_message.strip():
        sections.extend(["", extra_message.strip()])
    if footer.strip():
        sections.extend(["", footer.strip()])
    return "\n".join(sections)


def build_new_attendees_calendar_invite_body(
    *,
    item: Any,
    changes: dict[str, Any],
    account: Account,
    message: str = "",
) -> HTMLBody:
    """Тело календарного приглашения для новых участников (SendOnlyToChanged)."""
    subject = str(getattr(item, "subject", "") or "Совещание").strip()
    after = list(changes.get("after") or [])
    roster_pairs = resolve_attendee_pairs(after, item=item, account=account)
    text = build_new_attendees_notification_body(
        subject=subject,
        roster_pairs=roster_pairs,
        extra_message=message,
    )
    return plain_text_to_html(text)


def build_removed_attendees_calendar_body(
    *,
    item: Any,
    message: str = "",
) -> HTMLBody:
    """Тело уведомления об отмене участия (SendOnlyToChanged для удалённых)."""
    subject = str(getattr(item, "subject", "") or "Совещание").strip()
    text = build_removed_attendees_notification_body(
        subject=subject,
        extra_message=message,
    )
    return plain_text_to_html(text)


def send_plain_notification_email(
    account: Account,
    *,
    to_email: str,
    subject: str,
    body: str,
) -> None:
    message = Message(
        account=account,
        folder=account.sent,
        subject=subject.strip(),
        body=plain_text_to_html(body),
        to_recipients=[Mailbox(email_address=to_email.strip())],
    )
    message.send_and_save()


def existing_attendee_recipients(
    *,
    before: list[str],
    added: list[str],
    removed: list[str],
) -> list[str]:
    added_keys = {email.lower() for email in added}
    removed_keys = {email.lower() for email in removed}
    recipients: list[str] = []
    seen: set[str] = set()
    for email in before:
        key = email.lower()
        if key in added_keys or key in removed_keys or key in seen:
            continue
        if not is_notification_recipient(email):
            continue
        seen.add(key)
        recipients.append(email)
    return recipients


def send_attendee_update_notifications(
    *,
    account: Account,
    item: Any,
    changes: dict[str, Any],
    message: str = "",
    config: OutlookConfig | None = None,
) -> dict[str, Any]:
    del config
    subject = str(getattr(item, "subject", "") or "Совещание").strip()
    added = list(changes.get("added") or [])
    removed = list(changes.get("removed") or [])
    before = list(changes.get("before") or [])
    after = list(changes.get("after") or [])

    roster_pairs = resolve_attendee_pairs(after, item=item, account=account)
    added_pairs = resolve_attendee_pairs(added, item=item, account=account)
    removed_pairs = resolve_attendee_pairs(removed, item=item, account=account)

    protected = {email for email in [organizer_email(item)] if email}
    notified_existing: list[str] = []
    notified_new: list[str] = []
    notified_removed: list[str] = []
    errors: list[str] = []

    for email in existing_attendee_recipients(before=before, added=added, removed=removed):
        if email.lower() in protected:
            continue
        body = build_existing_attendees_notification_body(
            added_pairs=added_pairs,
            removed_pairs=removed_pairs,
            roster_pairs=roster_pairs,
            extra_message=message,
        )
        try:
            send_plain_notification_email(
                account,
                to_email=email,
                subject=f"Обновление состава участников: {subject}",
                body=body,
            )
            notified_existing.append(email)
        except Exception as error:
            errors.append(f"{email}: {error}")

    # Новым участникам текст уходит в календарном приглашении (SendOnlyToChanged).
    for email in added:
        if email.lower() in protected or not is_notification_recipient(email):
            continue
        notified_new.append(email)

    # Удалённым — в уведомлении об отмене участия (SendOnlyToChanged).
    for email in removed:
        if email.lower() in protected or not is_notification_recipient(email):
            continue
        notified_removed.append(email)

    return {
        "notified_existing": notified_existing,
        "notified_new": notified_new,
        "notified_removed": notified_removed,
        "notification_errors": errors,
    }


__all__ = [
    "INVITE_AGENT_FOOTER",
    "SEND_ONLY_TO_CHANGED",
    "attendee_line",
    "build_existing_attendees_notification_body",
    "build_new_attendees_calendar_invite_body",
    "build_removed_attendees_calendar_body",
    "build_removed_attendees_notification_body",
    "plain_text_to_html",
    "resolve_attendee_pair",
    "resolve_attendee_pairs",
    "send_attendee_update_notifications",
]
