"""
Перенос совещания в календаре Exchange (EWS) от имени ящика из outlook_config.

Для повторяющихся совещаний (серий):
  - --scope occurrence — перенести одно вхождение (нужны subject + start)
  - --scope series     — перенести всю серию целиком

Участникам уходит обновлённое приглашение с новым временем (как в Outlook).

Примеры:
  python -m app.tools.Outlook.reschedule_meeting --list --days 7
  python -m app.tools.Outlook.reschedule_meeting --subject "Регламент" --start "2026-07-14 16:00" --new-start "2026-07-14 17:00" --scope occurrence --yes
  python -m app.tools.Outlook.reschedule_meeting --subject "Регламент" --start "2026-07-14 16:00" --new-start "2026-07-15 16:00" --scope series --yes
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from typing import Any

from exchangelib.fields import WEEKDAY_NAMES
from exchangelib.items import SEND_ONLY_TO_ALL
from app.tools.Outlook.outlook_html_body import append_plain_text_to_html, plain_text_to_html
from exchangelib.recurrence import WeeklyPattern

from app.tools.Outlook.cancel_meeting import (
    list_meetings,
    meeting_to_dict,
    print_meeting,
    resolve_meeting,
    to_ews,
    to_local,
)
from app.tools.Outlook.meeting_series import RescheduleScope, resolve_reschedule_target
from app.tools.Outlook.outlook_config import OutlookConfig
from app.tools.Outlook.send_meeting_invite import load_config, parse_start, primary_smtp_address


def meeting_duration(item: Any, *, config: OutlookConfig) -> timedelta:
    if item.start and item.end:
        return to_local(item.end, config) - to_local(item.start, config)
    return timedelta(hours=1)


def resolve_new_end(
    *,
    config: OutlookConfig,
    new_start: datetime,
    item: Any,
    duration_minutes: int | None,
    new_end_value: str | None,
    tz_name: str,
) -> datetime:
    if new_end_value:
        return parse_start(new_end_value, tz_name)
    if duration_minutes is not None:
        return new_start + timedelta(minutes=max(duration_minutes, 1))
    return new_start + meeting_duration(item, config=config)


def _weekday_name(dt: datetime) -> str:
    return WEEKDAY_NAMES[dt.weekday()]


def _series_recurrence_update_fields(item: Any, *, new_start: datetime, config: OutlookConfig) -> list[str]:
    recurrence = getattr(item, "recurrence", None)
    if recurrence is None:
        return []

    local_start = to_local(new_start, config)
    pattern = getattr(recurrence, "pattern", None)
    if isinstance(pattern, WeeklyPattern):
        pattern.weekdays = [_weekday_name(local_start)]

    boundary = getattr(recurrence, "boundary", None)
    if boundary is not None and hasattr(boundary, "start"):
        boundary.start = local_start.date()

    return ["recurrence"]


def reschedule_meeting_item(
    item: Any,
    *,
    config: OutlookConfig,
    new_start: datetime,
    new_end: datetime,
    location: str | None = None,
    message: str = "",
    reschedule_scope: RescheduleScope = "occurrence",
    company_calendar_item_id: str | None = None,
    company_calendar_changekey: str | None = None,
) -> dict[str, Any]:
    if item.is_cancelled:
        raise RuntimeError(f"Совещание уже отменено: {item.subject}")

    target, target_kind, applied_scope = resolve_reschedule_target(
        item,
        scope=reschedule_scope,
    )
    if getattr(target, "is_cancelled", False):
        raise RuntimeError(f"Совещание уже отменено: {getattr(target, 'subject', '')}")

    new_start_local = to_local(new_start, config)
    new_end_local = to_local(new_end, config)
    if new_end_local <= new_start_local:
        raise ValueError("Конец совещания должен быть позже начала.")

    target.start = to_ews(new_start_local, config)
    target.end = to_ews(new_end_local, config)

    if location is not None:
        target.location = location.strip()

    if message.strip():
        note = message.strip()
        existing = str(target.body or "").strip()
        target.body = append_plain_text_to_html(existing, note)

    update_fields = ["start", "end"]
    if applied_scope == "series":
        update_fields.extend(_series_recurrence_update_fields(target, new_start=new_start, config=config))
    if location is not None:
        update_fields.append("location")
    if message.strip():
        update_fields.append("body")

    target.save(update_fields=update_fields, send_meeting_invitations=SEND_ONLY_TO_ALL)
    from app.tools.Outlook.company_calendar_sync import sync_meeting_to_company_calendar

    company_meta = sync_meeting_to_company_calendar(
        target,
        config=config,
        company_item_id=company_calendar_item_id,
        company_changekey=company_calendar_changekey,
    )
    return {
        "reschedule_scope": applied_scope,
        "target_kind": target_kind,
        "target_id": getattr(target, "id", None),
        "target_subject": getattr(target, "subject", None),
        **company_meta,
    }


def dispatch_reschedule_meeting(
    *,
    list_only: bool = False,
    days: int = 14,
    item_id: str = "",
    changekey: str = "",
    subject: str = "",
    start: str = "",
    new_start: str = "",
    new_end: str = "",
    duration_minutes: int | None = None,
    location: str | None = None,
    attendee: str = "",
    tolerance_minutes: int = 5,
    message: str = "",
    dry_run: bool = False,
    reschedule_scope: RescheduleScope = "occurrence",
    timezone: str | None = None,
    config: OutlookConfig | None = None,
    company_calendar_item_id: str | None = None,
    company_calendar_changekey: str | None = None,
) -> dict[str, Any]:
    """Переносит совещание или возвращает список встреч для API/агента."""
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

    if not new_start.strip():
        raise ValueError("Укажите new_start для переноса или list_only=true для просмотра.")

    if not item_id.strip() and (not subject.strip() or not start.strip()):
        raise ValueError("Укажите item_id либо subject и start для поиска совещания.")

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
        on_ambiguous="closest",
    )
    new_start_dt = parse_start(new_start, tz_name)
    new_end_dt = resolve_new_end(
        config=config,
        new_start=new_start_dt,
        item=item,
        duration_minutes=duration_minutes,
        new_end_value=new_end or None,
        tz_name=tz_name,
    )

    meeting = meeting_to_dict(item, config=config)
    result: dict[str, Any] = {
        "action": "reschedule",
        "calendar": calendar,
        "meeting": meeting,
        "new_start": to_local(new_start_dt, config).isoformat(),
        "new_end": to_local(new_end_dt, config).isoformat(),
        "reschedule_scope": reschedule_scope,
        "message": message,
    }
    if location is not None:
        result["location"] = location.strip()

    if dry_run:
        try:
            target, target_kind, applied_scope = resolve_reschedule_target(
                item,
                scope=reschedule_scope,
            )
        except RuntimeError as error:
            result["status"] = "dry_run"
            result["error"] = str(error)
            return result
        result["status"] = "dry_run"
        result["reschedule_scope"] = applied_scope
        result["target_kind"] = target_kind
        result["target_id"] = getattr(target, "id", None)
        return result

    reschedule_result = reschedule_meeting_item(
        item,
        config=config,
        new_start=new_start_dt,
        new_end=new_end_dt,
        location=location,
        message=message,
        reschedule_scope=reschedule_scope,
        company_calendar_item_id=company_calendar_item_id,
        company_calendar_changekey=company_calendar_changekey,
    )
    result["status"] = "rescheduled"
    result.update(reschedule_result)
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Перенос совещаний Exchange (EWS).")
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
        help="EWS ItemId совещания (из --list)",
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
        help="Тема совещания для поиска (частичное совпадение)",
    )
    parser.add_argument(
        "--start",
        help="Текущее начало для поиска: YYYY-MM-DD HH:MM",
    )
    parser.add_argument(
        "--new-start",
        help="Новое начало совещания: YYYY-MM-DD HH:MM",
    )
    parser.add_argument(
        "--new-end",
        help="Новый конец (если не задан — сохраняется длительность или --duration)",
    )
    parser.add_argument(
        "--duration",
        type=int,
        metavar="MIN",
        help="Новая длительность в минутах (иначе как у текущего совещания)",
    )
    parser.add_argument(
        "--location",
        "-l",
        help="Новое место проведения",
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
        help="Допуск по текущему времени начала, минут (по умолчанию 5)",
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
        help="Комментарий в уведомлении о переносе",
    )
    parser.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Выполнить перенос без подтверждения",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Только показать, что будет изменено",
    )
    parser.add_argument(
        "--tz",
        default=None,
        help="Часовой пояс для --start / --new-start (по умолчанию OUTLOOK_TIMEZONE из .env)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    args = build_parser().parse_args(argv)
    config = load_config()

    try:
        if args.list:
            result = dispatch_reschedule_meeting(
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

        if not args.new_start:
            print(
                "Укажите --new-start для переноса, либо --list для просмотра.",
                file=sys.stderr,
            )
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
            tz_name = args.tz or config.timezone
            new_start = parse_start(args.new_start, tz_name)
            new_end = resolve_new_end(
                config=config,
                new_start=new_start,
                item=item,
                duration_minutes=args.duration,
                new_end_value=args.new_end,
                tz_name=tz_name,
            )
            location = args.location if args.location is not None else None
            print(f"Календарь: {primary_smtp_address(config)}")
            print_meeting(item, config=config)
            print()
            print("Будет перенесено на:")
            print(f"   {to_local(new_start, config)} — {to_local(new_end, config)}")
            if location is not None:
                print(f"   Место: {location.strip() or '—'}")
            print("\nДобавьте --yes для переноса или --dry-run для проверки.")
            return 2

        result = dispatch_reschedule_meeting(
            item_id=args.id or "",
            changekey=args.changekey or "",
            subject=args.subject or "",
            start=args.start or "",
            new_start=args.new_start,
            new_end=args.new_end or "",
            duration_minutes=args.duration,
            location=args.location if args.location is not None else None,
            attendee=args.attendee or "",
            tolerance_minutes=max(args.tolerance, 0),
            message=args.message,
            dry_run=args.dry_run,
            reschedule_scope=args.scope,
            timezone=args.tz,
            config=config,
        )
        print(f"Календарь: {result['calendar']}")
        meeting = result["meeting"]
        print(
            f"{meeting['subject']}\n"
            f"   было: {meeting['start']} — {meeting['end']}\n"
            f"   станет: {result['new_start']} — {result['new_end']}"
        )
        if meeting.get("is_series"):
            print(f"   Серия: да (kind={meeting.get('kind')})")
        if result.get("reschedule_scope"):
            print(f"   Область переноса: {result['reschedule_scope']}")
        if args.dry_run:
            print("\n(dry-run: перенос не выполнен)")
            if result.get("error"):
                print(f"Ошибка dry-run: {result['error']}")
        else:
            if result["reschedule_scope"] == "series":
                print("\nСерия совещаний перенесена, обновлённое приглашение отправлено участникам.")
            else:
                print("\nСовещание перенесено, обновлённое приглашение отправлено участникам.")
        return 0
    except Exception as error:
        print(f"Ошибка: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
