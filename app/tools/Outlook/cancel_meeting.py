"""
Отмена совещания в календаре Exchange (EWS) от имени ящика из outlook_config.

Отмена рассылает уведомление участникам (как «Отменить собрание» в Outlook).

Примеры:
  python -m app.tools.Outlook.cancel_meeting --list --days 7
  python -m app.tools.Outlook.cancel_meeting --id "AQMkAD..." --changekey "DwAA..." --dry-run
  python -m app.tools.Outlook.cancel_meeting --id "AQMkAD..." --changekey "DwAA..." --yes
  python -m app.tools.Outlook.cancel_meeting --subject "Тестовое совещание" --start "2026-06-09 10:00" --yes
  python -m app.tools.Outlook.cancel_meeting --subject "Тестовое" --start "2026-06-09 10:00" --message "Переносится" --yes
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from exchangelib import EWSDateTime, EWSTimeZone, CalendarItem
from exchangelib.properties import HTMLBody

from app.tools.Outlook.outlook_config import OutlookConfig
from app.tools.Outlook.send_meeting_invite import (
    connect_account,
    load_config,
    parse_start,
    primary_smtp_address,
)


def ews_tz(config: OutlookConfig) -> EWSTimeZone:
    return EWSTimeZone.from_timezone(ZoneInfo(config.timezone))


def to_local(dt: datetime, config: OutlookConfig) -> datetime:
    return dt.astimezone(ZoneInfo(config.timezone))


def to_ews(dt: datetime, config: OutlookConfig) -> EWSDateTime:
    local = to_local(dt, config)
    tz = ews_tz(config)
    return EWSDateTime(
        local.year,
        local.month,
        local.day,
        local.hour,
        local.minute,
        local.second,
        tzinfo=tz,
    )


def format_attendees(item: Any) -> str:
    emails: list[str] = []
    for field in ("required_attendees", "optional_attendees", "resources"):
        for attendee in getattr(item, field, None) or []:
            mailbox = getattr(attendee, "mailbox", None)
            address = getattr(mailbox, "email_address", None) if mailbox else None
            if address:
                emails.append(str(address))
    return ", ".join(emails) if emails else "—"


def meeting_to_dict(item: Any, *, config: OutlookConfig) -> dict[str, Any]:
    organizer = None
    if item.organizer:
        organizer = getattr(item.organizer, "email_address", None) or str(item.organizer)
    return {
        "id": item.id,
        "changekey": item.changekey,
        "subject": item.subject or "",
        "start": str(to_local(item.start, config)) if item.start else "",
        "end": str(to_local(item.end, config)) if item.end else "",
        "location": item.location or "",
        "organizer": organizer,
        "attendees": format_attendees(item),
        "is_cancelled": bool(item.is_cancelled),
        "is_meeting": bool(
            getattr(item, "required_attendees", None) or getattr(item, "resources", None)
        ),
    }


def item_has_attendee(item: Any, attendee: str) -> bool:
    needle = attendee.strip().lower()
    if not needle:
        return True
    for field in ("required_attendees", "optional_attendees", "resources"):
        for person in getattr(item, field, None) or []:
            mailbox = getattr(person, "mailbox", None)
            address = (getattr(mailbox, "email_address", None) or "").lower()
            if needle in address:
                return True
    return False


def list_meetings(
    *,
    config: OutlookConfig,
    days: int,
    subject: str = "",
    attendee: str = "",
    include_cancelled: bool = False,
) -> list[Any]:
    account = connect_account(config)
    tz = ZoneInfo(config.timezone)
    start = to_ews(datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0), config)
    end = to_ews(datetime.now(tz) + timedelta(days=max(days, 1)), config)
    items = list(account.calendar.view(start=start, end=end, max_items=500))

    subject_norm = subject.strip().lower()
    result: list[Any] = []
    for item in items:
        if not include_cancelled and item.is_cancelled:
            continue
        if subject_norm and subject_norm not in (item.subject or "").lower():
            continue
        if not item_has_attendee(item, attendee):
            continue
        result.append(item)
    result.sort(key=lambda row: row.start or datetime.min.replace(tzinfo=tz))
    return result


def get_meeting_by_id(
    *,
    config: OutlookConfig,
    item_id: str,
    changekey: str = "",
) -> Any:
    account = connect_account(config)
    kwargs: dict[str, str] = {"id": item_id.strip()}
    if changekey.strip():
        kwargs["changekey"] = changekey.strip()
    items = list(account.fetch([CalendarItem(**kwargs)]))
    if not items:
        raise RuntimeError(f"Совещание не найдено по id: {item_id}")
    return items[0]


def find_meetings(
    *,
    config: OutlookConfig,
    subject: str,
    start: datetime,
    tolerance_minutes: int = 5,
    attendee: str = "",
) -> list[Any]:
    account = connect_account(config)
    window_start = to_ews(start - timedelta(minutes=tolerance_minutes), config)
    window_end = to_ews(start + timedelta(minutes=tolerance_minutes + 1), config)

    items = list(account.calendar.view(start=window_start, end=window_end, max_items=200))
    subject_norm = subject.strip().lower()
    matches: list[Any] = []
    for item in items:
        if item.is_cancelled:
            continue
        if subject_norm and subject_norm not in (item.subject or "").lower():
            continue
        if not item_has_attendee(item, attendee):
            continue
        if item.start:
            item_start = to_local(item.start, config)
            if abs((item_start - to_local(start, config)).total_seconds()) > tolerance_minutes * 60:
                continue
        matches.append(item)
    return matches


def cancel_meeting_item(item: Any, *, message: str = "") -> None:
    if item.is_cancelled:
        raise RuntimeError(f"Совещание уже отменено: {item.subject}")
    kwargs: dict[str, Any] = {}
    if message.strip():
        kwargs["body"] = HTMLBody(message.strip())
    item.cancel(**kwargs)


def resolve_meeting(
    *,
    config: OutlookConfig,
    item_id: str = "",
    changekey: str = "",
    subject: str = "",
    start: datetime | None = None,
    tolerance_minutes: int = 5,
    attendee: str = "",
) -> Any:
    if item_id.strip():
        return get_meeting_by_id(config=config, item_id=item_id, changekey=changekey)

    if not subject.strip() or start is None:
        raise ValueError("Укажите item_id либо subject и start.")

    matches = find_meetings(
        config=config,
        subject=subject,
        start=start,
        tolerance_minutes=tolerance_minutes,
        attendee=attendee,
    )
    if not matches:
        raise RuntimeError(
            f"Совещение не найдено: «{subject}», начало {start.isoformat()}"
        )
    if len(matches) > 1:
        details = [meeting_to_dict(item, config=config) for item in matches]
        raise RuntimeError(
            f"Найдено несколько совещаний ({len(matches)}), уточните subject, start или attendee. "
            f"Совпадения: {details}"
        )
    return matches[0]


def dispatch_cancel_meeting(
    *,
    list_only: bool = False,
    days: int = 14,
    item_id: str = "",
    changekey: str = "",
    subject: str = "",
    start: str = "",
    attendee: str = "",
    tolerance_minutes: int = 5,
    message: str = "",
    dry_run: bool = False,
    timezone: str | None = None,
    config: OutlookConfig | None = None,
) -> dict[str, Any]:
    """Списывает или отменяет совещание и возвращает JSON для API/агента."""
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

    if not item_id.strip() and (not subject.strip() or not start.strip()):
        raise ValueError("Укажите list_only=true, item_id либо subject и start.")

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

    if dry_run:
        return {
            "action": "cancel",
            "status": "dry_run",
            "calendar": calendar,
            "meeting": meeting,
            "message": message,
        }

    cancel_meeting_item(item, message=message)
    return {
        "action": "cancel",
        "status": "cancelled",
        "calendar": calendar,
        "meeting": meeting,
        "message": message,
    }


def print_meeting(item: Any, *, config: OutlookConfig, index: int | None = None, show_ids: bool = True) -> None:
    data = meeting_to_dict(item, config=config)
    prefix = f"{index}. " if index is not None else ""
    lines = [
        f"{prefix}{data['subject']}",
        f"   {data['start']} — {data['end']}",
        f"   Место: {data['location'] or '—'}",
        f"   Участники: {data['attendees']}",
    ]
    if show_ids:
        lines.append(f"   id: {data['id']}")
        if data["changekey"]:
            lines.append(f"   changekey: {data['changekey']}")
    print("\n".join(lines))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Отмена совещаний Exchange (EWS).")
    parser.add_argument(
        "--list",
        action="store_true",
        help="Показать совещания в календаре организатора",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=14,
        help="Сколько дней вперёд показывать для --list (по умолчанию 14)",
    )
    parser.add_argument(
        "--id",
        help="EWS ItemId совещания (из --list); отмена без --subject/--start",
    )
    parser.add_argument(
        "--changekey",
        help="EWS ChangeKey (рекомендуется вместе с --id)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Для --list: вывести JSON с id и changekey",
    )
    parser.add_argument(
        "--subject",
        "-s",
        help="Тема совещания (частичное совпадение, без учёта регистра)",
    )
    parser.add_argument(
        "--start",
        help="Начало совещания для поиска: YYYY-MM-DD HH:MM",
    )
    parser.add_argument(
        "--attendee",
        help="E-mail участника для уточнения, если совпадений несколько",
    )
    parser.add_argument(
        "--tolerance",
        type=int,
        default=5,
        metavar="MIN",
        help="Допуск по времени начала, минут (по умолчанию 5)",
    )
    parser.add_argument(
        "--message",
        "-m",
        default="",
        help="Комментарий в уведомлении об отмене",
    )
    parser.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Отменить без подтверждения",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Только показать, что будет отменено",
    )
    parser.add_argument(
        "--tz",
        default=None,
        help="Часовой пояс для --start (по умолчанию OUTLOOK_TIMEZONE из .env)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    args = build_parser().parse_args(argv)
    config = load_config()

    try:
        if args.list:
            result = dispatch_cancel_meeting(
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
                prefix = f"{index}. "
                print(
                    f"{prefix}{data['subject']}\n"
                    f"   {data['start']} — {data['end']}\n"
                    f"   Место: {data['location'] or '—'}\n"
                    f"   Участники: {data['attendees']}\n"
                    f"   id: {data['id']}"
                )
                if data["changekey"]:
                    print(f"   changekey: {data['changekey']}")
            return 0

        if args.id or (args.subject and args.start):
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
                print(f"Календарь: {primary_smtp_address(config)}")
                print_meeting(item, config=config)
                print("\nДобавьте --yes для отмены или --dry-run для проверки.")
                return 2

            result = dispatch_cancel_meeting(
                item_id=args.id or "",
                changekey=args.changekey or "",
                subject=args.subject or "",
                start=args.start or "",
                attendee=args.attendee or "",
                tolerance_minutes=max(args.tolerance, 0),
                message=args.message,
                dry_run=args.dry_run,
                timezone=args.tz,
                config=config,
            )
            print(f"Календарь: {result['calendar']}")
            meeting = result["meeting"]
            print(
                f"{meeting['subject']}\n"
                f"   {meeting['start']} — {meeting['end']}\n"
                f"   id: {meeting['id']}"
            )
            if args.dry_run:
                print("\n(dry-run: отмена не выполнена)")
            else:
                print("\nСовещание отменено, уведомление отправлено участникам.")
            return 0

        print("Укажите --id, либо --subject и --start, либо --list.", file=sys.stderr)
        return 2
    except Exception as error:
        print(f"Ошибка: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
