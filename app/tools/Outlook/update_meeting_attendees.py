"""
Изменение состава участников совещания в Exchange (EWS).

Можно добавить или удалить участников. Для серий:
  - --scope occurrence — только одно вхождение
  - --scope series     — вся серия

Примеры:
  python -m app.tools.Outlook.update_meeting_attendees --list --days 7
  python -m app.tools.Outlook.update_meeting_attendees \\
    --subject "Регламент" --start "2026-07-14 16:00" --add new.user@turbo-don.ru --yes
  python -m app.tools.Outlook.update_meeting_attendees \\
    --subject "Регламент" --start "2026-07-14 16:00" --add new.user@turbo-don.ru \\
    --scope series --yes
  python -m app.tools.Outlook.update_meeting_attendees \\
    --id "AQMkAD..." --remove old.user@turbo-don.ru --yes
  python -m app.tools.Outlook.update_meeting_attendees \\
    --subject "Регламент" --start "2026-07-14 16:00" --remove old.user@turbo-don.ru \\
    --scope series --yes
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Literal

from app.tools.Outlook.outlook_html_body import plain_text_to_html

from app.tools.Outlook.attendee_update_notifications import (
    SEND_ONLY_TO_CHANGED,
    build_new_attendees_calendar_invite_body,
    build_removed_attendees_calendar_body,
    send_attendee_update_notifications,
)

from app.tools.Outlook.cancel_meeting import (
    list_meetings,
    meeting_to_dict,
    print_meeting,
    resolve_meeting,
)
from app.tools.Outlook.meeting_series import AttendeesScope, resolve_attendees_target
from app.tools.Outlook.outlook_config import OutlookConfig
from app.tools.Outlook.send_meeting_invite import (
    connect_account,
    load_config,
    parse_start,
    primary_smtp_address,
    resolve_attendee,
)

AttendeeField = Literal["required_attendees", "optional_attendees"]
ATTENDEE_FIELDS: tuple[AttendeeField, ...] = ("required_attendees", "optional_attendees")


def normalize_emails(emails: list[str]) -> list[str]:
    unique: list[str] = []
    seen: set[str] = set()
    for raw in emails:
        email = raw.strip()
        if not email:
            continue
        key = email.lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(email)
    return unique


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


def list_attendee_emails(item: Any, *, field: AttendeeField) -> list[str]:
    emails: list[str] = []
    for attendee in getattr(item, field, None) or []:
        address = attendee_email(attendee)
        if address:
            emails.append(address)
    return emails


def all_attendee_emails(item: Any) -> list[str]:
    emails: list[str] = []
    for field in ATTENDEE_FIELDS:
        emails.extend(list_attendee_emails(item, field=field))
    return normalize_emails(emails)


def _filter_attendees(attendees: list[Any], *, remove: set[str]) -> list[Any]:
    kept: list[Any] = []
    for attendee in attendees:
        address = attendee_email(attendee)
        if address and address.lower() in remove:
            continue
        kept.append(attendee)
    return kept


def apply_attendee_changes(
    item: Any,
    *,
    add: list[str] | None = None,
    remove: list[str] | None = None,
) -> dict[str, Any]:
    add_emails = normalize_emails(add or [])
    remove_emails = normalize_emails(remove or [])
    if not add_emails and not remove_emails:
        raise ValueError("Укажите хотя бы одного участника в --add или --remove.")

    before = all_attendee_emails(item)
    protected = {email for email in [organizer_email(item)] if email}
    remove_keys = {
        email.lower()
        for email in remove_emails
        if email.lower() not in protected
    }
    skipped_remove = [
        email
        for email in remove_emails
        if email.lower() in protected
    ]

    existing_keys = {email.lower() for email in before}
    added: list[str] = []
    for email in add_emails:
        key = email.lower()
        if key in existing_keys:
            continue
        added.append(email)
        existing_keys.add(key)

    removed = [email for email in before if email.lower() in remove_keys]

    required = list(getattr(item, "required_attendees", None) or [])
    optional = list(getattr(item, "optional_attendees", None) or [])
    if remove_keys:
        required = _filter_attendees(required, remove=remove_keys)
        optional = _filter_attendees(optional, remove=remove_keys)

    required_keys = {
        (attendee_email(attendee) or "").lower()
        for attendee in required
    }
    for email in added:
        key = email.lower()
        if key in required_keys:
            continue
        required.append(resolve_attendee(email))
        required_keys.add(key)

    item.required_attendees = required
    item.optional_attendees = optional

    after = all_attendee_emails(item)
    return {
        "before": before,
        "after": after,
        "added": added,
        "removed": removed,
        "skipped_remove": skipped_remove,
    }


def _calendar_body_for_attendee_changes(
    *,
    target: Any,
    changes: dict[str, Any],
    account: Any,
    message: str,
    config: OutlookConfig,
    calendar_invite_body: str | None = None,
) -> Any | None:
    if changes.get("added") and not changes.get("removed"):
        if calendar_invite_body and calendar_invite_body.strip():
            return plain_text_to_html(calendar_invite_body.strip())
        return build_new_attendees_calendar_invite_body(
            item=target,
            changes=changes,
            account=account,
        )
    if calendar_invite_body and calendar_invite_body.strip():
        text = calendar_invite_body.strip()
        if message.strip():
            text = f"{message.strip()}\n\n{text}"
        return plain_text_to_html(text)
    if changes.get("removed"):
        return build_removed_attendees_calendar_body(
            item=target,
            message=message,
            config=config,
        )
    if changes.get("added"):
        return build_new_attendees_calendar_invite_body(
            item=target,
            changes=changes,
            account=account,
        )
    return None


def update_meeting_attendees_item(
    item: Any,
    *,
    add: list[str] | None = None,
    remove: list[str] | None = None,
    message: str = "",
    attendees_scope: AttendeesScope = "occurrence",
    stakeholder_emails: list[str] | None = None,
    config: OutlookConfig | None = None,
    company_calendar_item_id: str | None = None,
    company_calendar_changekey: str | None = None,
    calendar_invite_body: str | None = None,
) -> dict[str, Any]:
    if getattr(item, "is_cancelled", False):
        raise RuntimeError(f"Совещание уже отменено: {getattr(item, 'subject', '')}")

    target, target_kind, applied_scope = resolve_attendees_target(
        item,
        scope=attendees_scope,
    )
    if getattr(target, "is_cancelled", False):
        raise RuntimeError(f"Совещание уже отменено: {getattr(target, 'subject', '')}")

    remove_emails = normalize_emails(remove or [])
    add_emails = normalize_emails(add or [])
    before_emails = all_attendee_emails(target)
    resolved_config = config or load_config()
    account = getattr(target, "account", None) or connect_account(resolved_config)

    if calendar_invite_body and calendar_invite_body.strip():
        changes = apply_attendee_changes(target, add=add_emails, remove=remove_emails)
        update_fields: list[str] = ["required_attendees", "optional_attendees"]
        if changes["added"] or changes["removed"]:
            body = _calendar_body_for_attendee_changes(
                target=target,
                changes=changes,
                account=account,
                message=message,
                config=resolved_config,
                calendar_invite_body=calendar_invite_body,
            )
            if body is not None:
                target.body = body
                update_fields.append("body")
            target.save(
                update_fields=update_fields,
                send_meeting_invitations=SEND_ONLY_TO_CHANGED,
            )
    elif remove_emails and add_emails:
        removed_part = apply_attendee_changes(target, remove=remove_emails)
        if removed_part["removed"]:
            target.body = build_removed_attendees_calendar_body(
                item=target,
                message=message,
                config=resolved_config,
            )
            target.save(
                update_fields=["required_attendees", "optional_attendees", "body"],
                send_meeting_invitations=SEND_ONLY_TO_CHANGED,
            )
        added_part = apply_attendee_changes(target, add=add_emails)
        if added_part["added"]:
            target.body = build_new_attendees_calendar_invite_body(
                item=target,
                changes={"after": all_attendee_emails(target)},
                account=account,
            )
            target.save(
                update_fields=["required_attendees", "optional_attendees", "body"],
                send_meeting_invitations=SEND_ONLY_TO_CHANGED,
            )
        changes = {
            "before": before_emails,
            "after": all_attendee_emails(target),
            "added": added_part["added"],
            "removed": removed_part["removed"],
            "skipped_remove": removed_part["skipped_remove"],
        }
    else:
        changes = apply_attendee_changes(target, add=add_emails, remove=remove_emails)
        update_fields: list[str] = ["required_attendees", "optional_attendees"]
        if changes["removed"]:
            target.body = build_removed_attendees_calendar_body(
                item=target,
                message=message,
                config=resolved_config,
            )
            update_fields.append("body")
        elif changes["added"]:
            target.body = build_new_attendees_calendar_invite_body(
                item=target,
                changes=changes,
                account=account,
            )
            update_fields.append("body")

        target.save(
            update_fields=update_fields,
            send_meeting_invitations=SEND_ONLY_TO_CHANGED,
        )

    if not changes["added"] and not changes["removed"]:
        raise RuntimeError("Состав участников не изменился.")

    notification_result = send_attendee_update_notifications(
        account=account,
        item=target,
        changes=changes,
        message=message,
        config=resolved_config,
        stakeholder_emails=stakeholder_emails,
    )
    from app.tools.Outlook.company_calendar_sync import sync_meeting_to_company_calendar

    company_meta = sync_meeting_to_company_calendar(
        target,
        config=resolved_config,
        company_item_id=company_calendar_item_id,
        company_changekey=company_calendar_changekey,
    )

    return {
        "attendees_scope": applied_scope,
        "target_kind": target_kind,
        "target_id": getattr(target, "id", None),
        "target_subject": getattr(target, "subject", None),
        **changes,
        **notification_result,
        **company_meta,
    }


def dispatch_update_meeting_attendees(
    *,
    list_only: bool = False,
    days: int = 14,
    item_id: str = "",
    changekey: str = "",
    subject: str = "",
    start: str = "",
    attendee: str = "",
    tolerance_minutes: int = 5,
    add: list[str] | None = None,
    remove: list[str] | None = None,
    message: str = "",
    dry_run: bool = False,
    attendees_scope: AttendeesScope = "occurrence",
    timezone: str | None = None,
    config: OutlookConfig | None = None,
    stakeholder_emails: list[str] | None = None,
    company_calendar_item_id: str | None = None,
    company_calendar_changekey: str | None = None,
) -> dict[str, Any]:
    config = config or load_config()
    calendar = primary_smtp_address(config)

    if list_only:
        meetings = list_meetings(
            config=config,
            days=days,
            subject=subject,
            attendee=attendee,
        )
        return {
            "action": "list",
            "calendar": calendar,
            "meetings_count": len(meetings),
            "meetings": [meeting_to_dict(item, config=config) for item in meetings],
        }

    add_emails = normalize_emails(add or [])
    remove_emails = normalize_emails(remove or [])
    if not add_emails and not remove_emails:
        raise ValueError("Укажите add/remove или list_only=true.")

    if not item_id.strip() and (not subject.strip() or not start.strip()):
        raise ValueError("Укажите item_id либо subject и start.")

    tz_name = timezone or config.timezone
    start_dt = parse_start(start, tz_name) if start.strip() else None
    item = resolve_meeting(
        config=config,
        item_id=item_id,
        changekey=changekey,
        subject=subject,
        start=start_dt,
        tolerance_minutes=max(tolerance_minutes, 0),
        attendee=attendee,
    )
    meeting = meeting_to_dict(item, config=config)
    result: dict[str, Any] = {
        "action": "update_attendees",
        "calendar": calendar,
        "meeting": meeting,
        "add": add_emails,
        "remove": remove_emails,
        "attendees_scope": attendees_scope,
        "message": message,
    }

    if dry_run:
        try:
            target, target_kind, applied_scope = resolve_attendees_target(
                item,
                scope=attendees_scope,
            )
            preview = apply_attendee_changes(target, add=add_emails, remove=remove_emails)
        except RuntimeError as error:
            result["status"] = "dry_run"
            result["error"] = str(error)
            return result
        result["status"] = "dry_run"
        result["attendees_scope"] = applied_scope
        result["target_kind"] = target_kind
        result["target_id"] = getattr(target, "id", None)
        result.update(preview)
        return result

    update_result = update_meeting_attendees_item(
        item,
        add=add_emails,
        remove=remove_emails,
        message=message,
        attendees_scope=attendees_scope,
        stakeholder_emails=stakeholder_emails,
        config=config,
        company_calendar_item_id=company_calendar_item_id,
        company_calendar_changekey=company_calendar_changekey,
    )
    result["status"] = "updated"
    result.update(update_result)
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Добавить или удалить участников совещания Exchange (EWS)."
    )
    parser.add_argument("--list", action="store_true", help="Показать совещания")
    parser.add_argument("--days", type=int, default=14)
    parser.add_argument("--id", help="EWS ItemId совещания")
    parser.add_argument("--changekey", help="EWS ChangeKey")
    parser.add_argument("--json", action="store_true", help="JSON для --list")
    parser.add_argument("--subject", "-s", help="Тема для поиска")
    parser.add_argument("--start", help="Начало для поиска: YYYY-MM-DD HH:MM")
    parser.add_argument("--attendee", help="E-mail участника для уточнения поиска")
    parser.add_argument("--tolerance", type=int, default=5, metavar="MIN")
    parser.add_argument(
        "--add",
        action="append",
        default=[],
        metavar="EMAIL",
        help="Добавить участника (можно несколько раз)",
    )
    parser.add_argument(
        "--remove",
        action="append",
        default=[],
        metavar="EMAIL",
        help="Удалить участника (можно несколько раз)",
    )
    parser.add_argument(
        "--scope",
        choices=["occurrence", "series"],
        default="occurrence",
        help="occurrence — одно совещание; series — всю серию",
    )
    parser.add_argument("--message", "-m", default="", help="Комментарий участникам")
    parser.add_argument("--yes", "-y", action="store_true", help="Применить без подтверждения")
    parser.add_argument("--dry-run", action="store_true", help="Только показать изменения")
    parser.add_argument("--tz", default=None, help="Часовой пояс для --start")
    return parser


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    args = build_parser().parse_args(argv)
    config = load_config()

    try:
        if args.list:
            result = dispatch_update_meeting_attendees(
                list_only=True,
                days=args.days,
                subject=args.subject or "",
                attendee=args.attendee or "",
                config=config,
            )
            if args.json:
                print(json.dumps(result, ensure_ascii=False, indent=2))
                return 0
            print(f"Календарь: {result['calendar']}")
            print(f"Совещаний: {result['meetings_count']}")
            for index, data in enumerate(result["meetings"], start=1):
                print()
                print(
                    f"{index}. {data['subject']}\n"
                    f"   {data['start']} — {data['end']}\n"
                    f"   Участники: {data['attendees']}\n"
                    f"   id: {data['id']}"
                )
            return 0

        if not args.add and not args.remove:
            print("Укажите --add и/или --remove.", file=sys.stderr)
            return 2

        if not args.yes and not args.dry_run:
            item = resolve_meeting(
                config=config,
                item_id=args.id or "",
                changekey=args.changekey or "",
                subject=args.subject or "",
                start=parse_start(args.start, args.tz or config.timezone)
                if args.start
                else None,
                tolerance_minutes=max(args.tolerance, 0),
                attendee=args.attendee or "",
            )
            target, _, applied_scope = resolve_attendees_target(item, scope=args.scope)
            preview = apply_attendee_changes(
                target,
                add=normalize_emails(args.add),
                remove=normalize_emails(args.remove),
            )
            print(f"Календарь: {primary_smtp_address(config)}")
            print_meeting(item, config=config)
            print(f"Область изменения: {applied_scope}")
            print(f"Было: {', '.join(preview['before']) or '—'}")
            print(f"Станет: {', '.join(preview['after']) or '—'}")
            if preview["added"]:
                print(f"Добавить: {', '.join(preview['added'])}")
            if preview["removed"]:
                print(f"Удалить: {', '.join(preview['removed'])}")
            print("\nДобавьте --yes для применения или --dry-run для проверки.")
            return 2

        result = dispatch_update_meeting_attendees(
            item_id=args.id or "",
            changekey=args.changekey or "",
            subject=args.subject or "",
            start=args.start or "",
            attendee=args.attendee or "",
            tolerance_minutes=max(args.tolerance, 0),
            add=args.add,
            remove=args.remove,
            message=args.message,
            dry_run=args.dry_run,
            attendees_scope=args.scope,
            timezone=args.tz,
            config=config,
        )
        print(f"Календарь: {result['calendar']}")
        meeting = result["meeting"]
        print(f"{meeting['subject']}\n   {meeting['start']} — {meeting['end']}")
        print(f"Было: {', '.join(result.get('before') or []) or '—'}")
        print(f"Стало: {', '.join(result.get('after') or []) or '—'}")
        if meeting.get("is_series"):
            print(f"Серия: да (kind={meeting.get('kind')})")
        if result.get("attendees_scope"):
            print(f"Область изменения: {result['attendees_scope']}")
        if args.dry_run:
            print("\n(dry-run: изменения не применены)")
            if result.get("error"):
                print(f"Ошибка dry-run: {result['error']}")
        else:
            if result.get("attendees_scope") == "series":
                print("\nСостав участников серии обновлён, уведомления отправлены.")
            else:
                print("\nСостав участников обновлён, уведомления отправлены.")
        return 0
    except Exception as error:
        print(f"Ошибка: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
