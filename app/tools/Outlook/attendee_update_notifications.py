"""Тексты и рассылка уведомлений при изменении состава совещания."""

from __future__ import annotations

from typing import Any, Literal

from exchangelib import Account, Message
from exchangelib.items import SEND_ONLY_TO_CHANGED
from exchangelib.properties import HTMLBody, Mailbox

from app.services.meeting_invite_format import format_invite_body
from app.services.meeting_slot import format_slot_label
from app.tools.mail_templates import INVITE_AGENT_FOOTER, invite_agent_footer, render_mail_template
from app.tools.Outlook.company_calendar_sync import is_company_calendar_email
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
    if is_company_calendar_email(normalized):
        return False
    if _is_resource_calendar_email(normalized):
        return False
    return normalized not in _room_name_by_email()


def format_meeting_datetime_label(item: Any, *, config: OutlookConfig | None = None) -> str:
    """Метка даты и времени совещания для текстов уведомлений."""
    from app.tools.Outlook.cancel_meeting import to_local
    from app.tools.Outlook.send_meeting_invite import load_config

    resolved_config = config or load_config()
    start = getattr(item, "start", None)
    end = getattr(item, "end", None)
    if start and end:
        start_iso = to_local(start, resolved_config).isoformat()
        end_iso = to_local(end, resolved_config).isoformat()
        return format_slot_label(start_iso, end_iso)
    if start:
        return to_local(start, resolved_config).strftime("%d.%m.%Y, %H:%M")
    return ""


def _lines_block(title: str, lines: list[str]) -> str:
    if not lines:
        return ""
    return "\n".join([title, *lines])


def _optional_message_block(message: str) -> str:
    return message.strip()


def build_existing_attendees_notification_body(
    *,
    subject: str = "",
    slot_label: str = "",
    added_pairs: list[AttendeePair],
    removed_pairs: list[AttendeePair],
    roster_pairs: list[AttendeePair],
    extra_message: str = "",
    footer: str | None = None,
) -> str:
    footer_text = invite_agent_footer() if footer is None else footer
    topic = subject.strip() or "Совещание"
    slot_text = slot_label.strip()
    roster_lines = attendee_lines(roster_pairs)
    extra = _optional_message_block(extra_message)
    footer_value = footer_text.strip()

    if slot_text:
        added_section = _lines_block("Добавленные участники:", attendee_lines(added_pairs))
        removed_section = _lines_block("Удаленные участники:", attendee_lines(removed_pairs))
        return render_mail_template(
            "attendee_roster_changed_with_slot",
            slot_label=slot_text,
            subject=topic,
            roster_lines="\n".join(roster_lines),
            added_section=added_section,
            removed_section=removed_section,
            extra_message=extra,
            footer=footer_value,
        )

    new_section = _lines_block("Новые участники:", attendee_lines(added_pairs))
    removed_section = _lines_block("Удаленные участники:", attendee_lines(removed_pairs))
    return render_mail_template(
        "attendee_roster_changed_generic",
        new_participants_section=new_section,
        removed_participants_section=removed_section,
        roster_lines="\n".join(roster_lines),
        extra_message=extra,
        footer=footer_value,
    )


def build_new_attendees_notification_body(
    *,
    subject: str,
    roster_pairs: list[AttendeePair],
    extra_message: str = "",
    footer: str | None = None,
) -> str:
    footer_text = invite_agent_footer() if footer is None else footer
    return render_mail_template(
        "attendee_added",
        subject=subject.strip(),
        roster_lines="\n".join(attendee_lines(roster_pairs)),
        extra_message=_optional_message_block(extra_message),
        footer=footer_text.strip(),
    )


def build_removed_attendees_notification_body(
    *,
    subject: str,
    slot_label: str = "",
    extra_message: str = "",
    footer: str | None = None,
) -> str:
    footer_text = invite_agent_footer() if footer is None else footer
    topic = subject.strip() or "Совещание"
    slot_text = slot_label.strip()
    extra = _optional_message_block(extra_message)
    footer_value = footer_text.strip()
    if slot_text:
        return render_mail_template(
            "attendee_removed_with_slot",
            slot_label=slot_text,
            subject=topic,
            extra_message=extra,
            footer=footer_value,
        )
    return render_mail_template(
        "attendee_removed_generic",
        subject=topic,
        extra_message=extra,
        footer=footer_value,
    )


def build_meeting_roster_calendar_body(
    *,
    item: Any,
    changes: dict[str, Any],
    account: Account,
) -> HTMLBody:
    """Стандартное описание совещания для календарной записи (актуальный состав)."""
    after = list(changes.get("after") or [])
    roster_pairs = resolve_attendee_pairs(after, item=item, account=account)
    text = format_invite_body(roster_pairs, footer=invite_agent_footer())
    return plain_text_to_html(text)


def build_new_attendees_calendar_invite_body(
    *,
    item: Any,
    changes: dict[str, Any],
    account: Account,
    message: str = "",
) -> HTMLBody:
    """Обратная совместимость: описание совещания с актуальным составом."""
    del message
    return build_meeting_roster_calendar_body(
        item=item,
        changes=changes,
        account=account,
    )


def build_removed_attendees_calendar_body(
    *,
    item: Any,
    message: str = "",
    config: OutlookConfig | None = None,
) -> HTMLBody:
    """HTML-тело письма об исключении (не для описания календарного события)."""
    subject = str(getattr(item, "subject", "") or "Совещание").strip()
    text = build_removed_attendees_notification_body(
        subject=subject,
        slot_label=format_meeting_datetime_label(item, config=config),
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


def stakeholder_notification_recipients(
    *,
    stakeholder_emails: list[str] | None,
    before: list[str],
    added: list[str],
    removed: list[str],
) -> list[str]:
    if stakeholder_emails:
        recipients: list[str] = []
        seen: set[str] = set()
        for email in stakeholder_emails:
            key = email.strip().lower()
            if not key or key in seen or not is_notification_recipient(email):
                continue
            seen.add(key)
            recipients.append(email.strip())
        return recipients
    return existing_attendee_recipients(before=before, added=added, removed=removed)


def send_attendee_update_notifications(
    *,
    account: Account,
    item: Any,
    changes: dict[str, Any],
    message: str = "",
    config: OutlookConfig | None = None,
    stakeholder_emails: list[str] | None = None,
) -> dict[str, Any]:
    subject = str(getattr(item, "subject", "") or "Совещание").strip()
    added = list(changes.get("added") or [])
    removed = list(changes.get("removed") or [])
    before = list(changes.get("before") or [])
    after = list(changes.get("after") or [])
    slot_label = format_meeting_datetime_label(item, config=config)

    roster_pairs = resolve_attendee_pairs(after, item=item, account=account)
    added_pairs = resolve_attendee_pairs(added, item=item, account=account)
    removed_pairs = resolve_attendee_pairs(removed, item=item, account=account)

    protected = {email for email in [organizer_email(item)] if email}
    notified_existing: list[str] = []
    notified_new: list[str] = []
    notified_removed: list[str] = []
    errors: list[str] = []

    if added or removed:
        composition_recipients = stakeholder_notification_recipients(
            stakeholder_emails=stakeholder_emails,
            before=before,
            added=added,
            removed=removed,
        )
    else:
        composition_recipients = existing_attendee_recipients(
            before=before,
            added=added,
            removed=removed,
        )

    for email in composition_recipients:
        if email.lower() in protected:
            continue
        body = build_existing_attendees_notification_body(
            subject=subject,
            slot_label=slot_label if (added or removed) else "",
            added_pairs=added_pairs,
            removed_pairs=removed_pairs,
            roster_pairs=roster_pairs,
            extra_message=message,
        )
        try:
            send_plain_notification_email(
                account,
                to_email=email,
                subject=render_mail_template(
                    "notification_subject_roster_update",
                    subject=subject,
                ),
                body=body,
            )
            notified_existing.append(email)
        except Exception as error:
            errors.append(f"{email}: {error}")

    for email in added:
        if email.lower() in protected or not is_notification_recipient(email):
            continue
        body = build_new_attendees_notification_body(
            subject=subject,
            roster_pairs=roster_pairs,
            extra_message=message,
        )
        try:
            send_plain_notification_email(
                account,
                to_email=email,
                subject=render_mail_template(
                    "notification_subject_attendee_added",
                    subject=subject,
                ),
                body=body,
            )
            notified_new.append(email)
        except Exception as error:
            errors.append(f"{email}: {error}")

    for email in removed:
        if email.lower() in protected or not is_notification_recipient(email):
            continue
        body = build_removed_attendees_notification_body(
            subject=subject,
            slot_label=slot_label,
            extra_message=message,
        )
        try:
            send_plain_notification_email(
                account,
                to_email=email,
                subject=render_mail_template(
                    "notification_subject_attendee_removed",
                    subject=subject,
                ),
                body=body,
            )
            notified_removed.append(email)
        except Exception as error:
            errors.append(f"{email}: {error}")

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
    "build_meeting_roster_calendar_body",
    "build_new_attendees_calendar_invite_body",
    "build_removed_attendees_calendar_body",
    "build_removed_attendees_notification_body",
    "format_meeting_datetime_label",
    "resolve_attendee_pair",
    "resolve_attendee_pairs",
    "send_attendee_update_notifications",
]
