"""
Отмена совещания в календаре Exchange (EWS) от имени ящика из outlook_config.

Для повторяющихся совещаний (серий):
  - --scope occurrence — отменить одно вхождение (нужны subject + start)
  - --scope series     — отменить всю серию целиком

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
from exchangelib.errors import ErrorItemNotFound
from exchangelib.properties import HTMLBody

from app.tools.Outlook.meeting_series import (
    CancelScope,
    meeting_series_fields,
    resolve_cancel_target,
)
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


def _ensure_calendar_item(item: Any, *, context: str) -> Any:
    if isinstance(item, ErrorItemNotFound):
        raise RuntimeError(f"Совещание не найдено: {context}")
    if not getattr(item, "id", None):
        raise RuntimeError(f"Совещание не найдено: {context}")
    return item


def meeting_to_dict(item: Any, *, config: OutlookConfig) -> dict[str, Any]:
    item = _ensure_calendar_item(item, context="некорректный ответ Exchange")
    organizer = None
    organizer_obj = getattr(item, "organizer", None)
    if organizer_obj is not None:
        organizer = getattr(organizer_obj, "email_address", None) or str(organizer_obj)
    return {
        "id": item.id,
        "changekey": item.changekey,
        "subject": item.subject or "",
        "start": str(to_local(item.start, config)) if item.start else "",
        "end": str(to_local(item.end, config)) if item.end else "",
        "location": item.location or "",
        "organizer": organizer,
        "attendees": format_attendees(item),
        "is_cancelled": bool(getattr(item, "is_cancelled", False)),
        "is_meeting": bool(
            getattr(item, "required_attendees", None) or getattr(item, "resources", None)
        ),
        **meeting_series_fields(item),
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
    return _ensure_calendar_item(items[0], context=f"id {item_id}")


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


def find_meetings_by_subject_on_day(
    *,
    config: OutlookConfig,
    subject: str,
    day: datetime,
    attendee: str = "",
    prefer_start: datetime | None = None,
) -> list[Any]:
    account = connect_account(config)
    local_day = to_local(day, config)
    day_start = local_day.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)
    window_start = to_ews(day_start, config)
    window_end = to_ews(day_end, config)

    items = list(account.calendar.view(start=window_start, end=window_end, max_items=500))
    subject_norm = subject.strip().lower()
    matches: list[Any] = []
    for item in items:
        if getattr(item, "is_cancelled", False):
            continue
        if subject_norm and subject_norm not in (item.subject or "").lower():
            continue
        if not item_has_attendee(item, attendee):
            continue
        matches.append(item)

    if prefer_start is not None and len(matches) > 1:
        target = to_local(prefer_start, config)
        matches.sort(
            key=lambda row: abs(
                (to_local(row.start, config) - target).total_seconds()
            )
            if row.start
            else float("inf")
        )
        return [matches[0]]
    return matches


def cancel_meeting_item(
    item: Any,
    *,
    message: str = "",
    cancel_scope: CancelScope = "occurrence",
) -> dict[str, Any]:
    item = _ensure_calendar_item(item, context="некорректный ответ Exchange")
    if getattr(item, "is_cancelled", False):
        raise RuntimeError(f"Совещание уже отменено: {getattr(item, 'subject', '')}")

    target, target_kind, applied_scope = resolve_cancel_target(item, scope=cancel_scope)
    if getattr(target, "is_cancelled", False):
        raise RuntimeError(f"Серия уже отменена: {getattr(target, 'subject', '')}")

    kwargs: dict[str, Any] = {}
    if message.strip():
        kwargs["body"] = HTMLBody(message.strip())
    target.cancel(**kwargs)
    return {
        "cancel_scope": applied_scope,
        "target_kind": target_kind,
        "target_id": getattr(target, "id", None),
        "target_subject": getattr(target, "subject", None),
    }


def resolve_meeting(
    *,
    config: OutlookConfig,
    item_id: str = "",
    changekey: str = "",
    subject: str = "",
    start: datetime | None = None,
    tolerance_minutes: int = 5,
    attendee: str = "",
    match_mode: str = "exact",
) -> Any:
    if item_id.strip():
        return get_meeting_by_id(config=config, item_id=item_id, changekey=changekey)

    if not subject.strip() or start is None:
        raise ValueError("Укажите item_id либо subject и start.")

    if match_mode == "day":
        matches = find_meetings_by_subject_on_day(
            config=config,
            subject=subject,
            day=start,
            attendee=attendee,
            prefer_start=start,
        )
    else:
        matches = find_meetings(
            config=config,
            subject=subject,
            start=start,
            tolerance_minutes=tolerance_minutes,
            attendee=attendee,
        )
    if not matches:
        raise RuntimeError(
            f"Совещание не найдено: «{subject}», начало {start.isoformat()}"
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
    match_mode: str = "exact",
    message: str = "",
    dry_run: bool = False,
    cancel_scope: CancelScope = "occurrence",
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
        match_mode=match_mode,
    )
    meeting = meeting_to_dict(item, config=config)

    if dry_run:
        try:
            target, target_kind, applied_scope = resolve_cancel_target(
                item,
                scope=cancel_scope,
            )
        except RuntimeError as error:
            return {
                "action": "cancel",
                "status": "dry_run",
                "calendar": calendar,
                "meeting": meeting,
                "cancel_scope": cancel_scope,
                "error": str(error),
                "message": message,
            }
        return {
            "action": "cancel",
            "status": "dry_run",
            "calendar": calendar,
            "meeting": meeting,
            "cancel_scope": applied_scope,
            "target_kind": target_kind,
            "target_id": getattr(target, "id", None),
            "message": message,
        }

    cancel_result = cancel_meeting_item(item, message=message, cancel_scope=cancel_scope)
    return {
        "action": "cancel",
        "status": "cancelled",
        "calendar": calendar,
        "meeting": meeting,
        "message": message,
        **cancel_result,
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
        "--scope",
        choices=["occurrence", "series"],
        default="occurrence",
        help="occurrence — одно совещание из серии; series — всю серию целиком",
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
                cancel_scope=args.scope,
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
            if meeting.get("is_series"):
                print(f"   Серия: да (kind={meeting.get('kind')})")
            if result.get("cancel_scope"):
                print(f"   Область отмены: {result['cancel_scope']}")
            if args.dry_run:
                print("\n(dry-run: отмена не выполнена)")
                if result.get("error"):
                    print(f"Ошибка dry-run: {result['error']}")
            else:
                if result["cancel_scope"] == "series":
                    print("\nСерия совещаний отменена, уведомление отправлено участникам.")
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
