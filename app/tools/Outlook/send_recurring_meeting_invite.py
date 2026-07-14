"""
Отправка повторяющегося приглашения на совещание через Exchange (EWS).

Поддерживаемые паттерны:
  - weekly   — еженедельно (по одному или нескольким дням недели)
  - daily    — ежедневно
  - monthly  — ежемесячно в указанный день месяца

Граница серии:
  - occurrences — фиксированное число встреч
  - end_date    — до даты включительно
  - no_end      — без окончания

Примеры:
  python -m app.tools.Outlook.send_recurring_meeting_invite \\
    --to user@company.ru --subject "Регламент" --start "2026-07-14 16:00" \\
    --duration 30 --pattern weekly --weekday Tuesday --occurrences 3

  python -m app.tools.Outlook.send_recurring_meeting_invite \\
    --to a@co.ru --to b@co.ru -s "Стендап" --start "2026-07-14 09:00" \\
    --pattern daily --interval 1 --end 2026-08-14
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta
from typing import Any, Literal

from exchangelib import CalendarItem
from exchangelib.items import SEND_ONLY_TO_ALL
from exchangelib.recurrence import (
    AbsoluteMonthlyPattern,
    DailyPattern,
    Recurrence,
    WeeklyPattern,
)

from app.tools.Outlook.outlook_config import OutlookConfig, build_outlook_config
from app.tools.Outlook.outlook_html_body import plain_text_to_html
from app.tools.Outlook.outlook_meeting_link import calendar_item_outlook_meta
from app.tools.Outlook.send_meeting_invite import (
    connect_account,
    load_config,
    parse_start,
    primary_smtp_address,
    resolve_attendee,
    resolve_resource,
)

RecurrencePattern = Literal["weekly", "daily", "monthly"]
RecurrenceEndType = Literal["occurrences", "end_date", "no_end"]

WEEKDAY_NAMES = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)

WEEKDAY_TO_INDEX = {name: index for index, name in enumerate(WEEKDAY_NAMES)}


def normalize_weekdays(weekdays: list[str] | None) -> list[str]:
    if not weekdays:
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in weekdays:
        name = raw.strip()
        if not name:
            continue
        if name not in WEEKDAY_NAMES:
            raise ValueError(
                f"Неизвестный день недели «{name}». "
                f"Допустимо: {', '.join(WEEKDAY_NAMES)}"
            )
        if name not in seen:
            seen.add(name)
            normalized.append(name)
    return normalized


def weekday_mismatch_warning(*, start: datetime, weekdays: list[str]) -> str | None:
    if not weekdays:
        return None
    actual = WEEKDAY_NAMES[start.weekday()]
    if len(weekdays) == 1 and weekdays[0] != actual:
        return (
            f"Дата начала ({actual}) не совпадает с днём повторения ({weekdays[0]}). "
            "Exchange может сдвинуть первое вхождение серии."
        )
    if len(weekdays) > 1 and actual not in weekdays:
        return (
            f"Дата начала ({actual}) не входит в дни повторения "
            f"({', '.join(weekdays)}). Exchange может сдвинуть первое вхождение."
        )
    return None


def build_recurrence(
    *,
    pattern: RecurrencePattern,
    start: datetime,
    interval: int = 1,
    weekdays: list[str] | None = None,
    day_of_month: int | None = None,
    end_type: RecurrenceEndType = "occurrences",
    occurrences: int | None = None,
    end: date | datetime | None = None,
) -> Recurrence:
    if interval < 1:
        raise ValueError("Интервал повторения должен быть >= 1")

    if pattern == "weekly":
        normalized_weekdays = normalize_weekdays(weekdays)
        if not normalized_weekdays:
            normalized_weekdays = [WEEKDAY_NAMES[start.weekday()]]
        recurrence_pattern = WeeklyPattern(
            interval=interval,
            weekdays=normalized_weekdays,
        )
    elif pattern == "daily":
        recurrence_pattern = DailyPattern(interval=interval)
    elif pattern == "monthly":
        month_day = day_of_month if day_of_month is not None else start.day
        if month_day < 1 or month_day > 31:
            raise ValueError("day_of_month должен быть в диапазоне 1–31")
        recurrence_pattern = AbsoluteMonthlyPattern(
            interval=interval,
            day_of_month=month_day,
        )
    else:
        raise ValueError(f"Неизвестный паттерн повторения: {pattern}")

    start_date = start.date()
    if end_type == "occurrences":
        if occurrences is None or occurrences < 1:
            raise ValueError("Для end_type=occurrences укажите occurrences >= 1")
        return Recurrence(
            pattern=recurrence_pattern,
            start=start_date,
            number=occurrences,
        )
    if end_type == "end_date":
        if end is None:
            raise ValueError("Для end_type=end_date укажите end (YYYY-MM-DD)")
        end_date = end.date() if isinstance(end, datetime) else end
        if end_date < start_date:
            raise ValueError("Дата окончания серии не может быть раньше даты начала")
        return Recurrence(
            pattern=recurrence_pattern,
            start=start_date,
            end=end_date,
        )
    if end_type == "no_end":
        return Recurrence(pattern=recurrence_pattern, start=start_date)
    raise ValueError(f"Неизвестный тип окончания серии: {end_type}")


def recurrence_summary(
    *,
    pattern: RecurrencePattern,
    interval: int,
    weekdays: list[str] | None,
    day_of_month: int | None,
    end_type: RecurrenceEndType,
    occurrences: int | None,
    end: date | datetime | None,
) -> dict[str, Any]:
    normalized_weekdays = normalize_weekdays(weekdays) if pattern == "weekly" else []
    return {
        "pattern": pattern,
        "interval": interval,
        "weekdays": normalized_weekdays or None,
        "day_of_month": day_of_month,
        "end_type": end_type,
        "occurrences": occurrences,
        "end": (
            end.date().isoformat()
            if isinstance(end, datetime)
            else end.isoformat()
            if isinstance(end, date)
            else None
        ),
    }


def send_recurring_meeting_invite(
    *,
    config: OutlookConfig,
    attendees: list[str],
    subject: str,
    start: datetime,
    duration_minutes: int,
    pattern: RecurrencePattern,
    interval: int = 1,
    weekdays: list[str] | None = None,
    day_of_month: int | None = None,
    end_type: RecurrenceEndType = "occurrences",
    occurrences: int | None = None,
    end: date | datetime | None = None,
    body: str = "",
    location: str = "",
    resources: list[str] | None = None,
) -> CalendarItem:
    people = [person.strip() for person in attendees if person.strip()]
    if not people:
        raise ValueError("Не указан ни один участник.")

    account = connect_account(config)
    meeting_end = start + timedelta(minutes=duration_minutes)
    recurrence = build_recurrence(
        pattern=pattern,
        start=start,
        interval=interval,
        weekdays=weekdays,
        day_of_month=day_of_month,
        end_type=end_type,
        occurrences=occurrences,
        end=end,
    )
    room_resources = [email.strip() for email in (resources or []) if email.strip()]

    invite_body = plain_text_to_html(body or subject)
    item = CalendarItem(
        account=account,
        folder=account.calendar,
        subject=subject,
        body=invite_body,
        start=start,
        end=meeting_end,
        location=location,
        recurrence=recurrence,
        required_attendees=[resolve_attendee(person) for person in people],
        resources=[resolve_resource(room) for room in room_resources],
    )
    item.save(send_meeting_invitations=SEND_ONLY_TO_ALL)
    return item


def dispatch_recurring_meeting_invite(
    *,
    attendee: str,
    subject: str,
    start: str,
    duration_minutes: int = 60,
    pattern: RecurrencePattern = "weekly",
    interval: int = 1,
    weekdays: list[str] | None = None,
    day_of_month: int | None = None,
    end_type: RecurrenceEndType = "occurrences",
    occurrences: int | None = 3,
    end: str | None = None,
    body: str = "",
    location: str = "",
    resources: list[str] | None = None,
    attendees: list[str] | None = None,
    timezone: str | None = None,
    config: OutlookConfig | None = None,
) -> dict[str, Any]:
    """Создаёт повторяющееся совещание и возвращает JSON-структуру для API/агента."""
    config = config or load_config()
    tz_name = timezone or config.timezone
    start_dt = parse_start(start, tz_name)
    people = [person.strip() for person in (attendees or [attendee]) if person.strip()]
    if not people:
        raise ValueError("Не указан ни один участник (attendee / attendees).")

    end_value: date | None = None
    if end:
        end_value = parse_start(end, tz_name).date()

    item = send_recurring_meeting_invite(
        config=config,
        attendees=people,
        subject=subject,
        start=start_dt,
        duration_minutes=duration_minutes,
        pattern=pattern,
        interval=interval,
        weekdays=weekdays,
        day_of_month=day_of_month,
        end_type=end_type,
        occurrences=occurrences,
        end=end_value,
        body=body,
        location=location,
        resources=resources,
    )
    end_dt = start_dt + timedelta(minutes=duration_minutes)
    outlook_meta = calendar_item_outlook_meta(item, config)
    room_resources = [email.strip() for email in (resources or []) if email.strip()]
    warning = weekday_mismatch_warning(
        start=start_dt,
        weekdays=normalize_weekdays(weekdays) if pattern == "weekly" else [],
    )
    return {
        "status": "sent",
        "from": primary_smtp_address(config),
        "login": config.email,
        "attendees": people,
        "subject": subject,
        "start": start_dt.isoformat(),
        "end": end_dt.isoformat(),
        "duration_minutes": duration_minutes,
        "location": location,
        "resources": room_resources,
        "timezone": tz_name,
        "recurrence": str(item.recurrence),
        "recurrence_summary": recurrence_summary(
            pattern=pattern,
            interval=interval,
            weekdays=weekdays,
            day_of_month=day_of_month,
            end_type=end_type,
            occurrences=occurrences,
            end=end_value,
        ),
        "series": {
            "is_series": True,
            "series_master_id": outlook_meta.get("outlook_item_id"),
            "pattern": pattern,
            "interval": interval,
            "weekdays": weekdays,
            "end_type": end_type,
            "occurrences": occurrences,
            "end": end_value.isoformat() if end_value else None,
        },
        "warning": warning,
        **outlook_meta,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Создать повторяющееся совещание в Outlook через Exchange."
    )
    parser.add_argument(
        "--to",
        action="append",
        required=True,
        metavar="EMAIL",
        help="E-mail приглашаемого (можно несколько раз)",
    )
    parser.add_argument("--subject", "-s", required=True, help="Тема совещания")
    parser.add_argument(
        "--start",
        required=True,
        help="Первое совещание: YYYY-MM-DD HH:MM или DD.MM.YYYY HH:MM",
    )
    parser.add_argument(
        "--duration",
        type=int,
        default=60,
        metavar="MIN",
        help="Длительность в минутах (по умолчанию 60)",
    )
    parser.add_argument(
        "--pattern",
        choices=["weekly", "daily", "monthly"],
        default="weekly",
        help="Паттерн повторения",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=1,
        help="Интервал: каждые N недель/дней/месяцев",
    )
    parser.add_argument(
        "--weekday",
        action="append",
        default=[],
        choices=list(WEEKDAY_NAMES),
        help="День недели для weekly (можно несколько раз)",
    )
    parser.add_argument(
        "--day-of-month",
        type=int,
        default=None,
        metavar="DAY",
        help="День месяца для monthly (по умолчанию — день из --start)",
    )
    end_group = parser.add_mutually_exclusive_group()
    end_group.add_argument(
        "--occurrences",
        type=int,
        default=3,
        help="Сколько встреч в серии (по умолчанию 3)",
    )
    end_group.add_argument(
        "--end",
        metavar="DATE",
        help="Дата окончания серии: YYYY-MM-DD",
    )
    end_group.add_argument(
        "--no-end",
        action="store_true",
        help="Серия без даты окончания",
    )
    parser.add_argument("--body", "-b", default="", help="Текст приглашения")
    parser.add_argument("--location", "-l", default="", help="Место проведения")
    parser.add_argument(
        "--resource",
        action="append",
        default=[],
        metavar="EMAIL",
        help="E-mail переговорной",
    )
    parser.add_argument(
        "--room",
        action="append",
        default=[],
        metavar="EMAIL",
        help="Синоним --resource",
    )
    parser.add_argument(
        "--tz",
        default=None,
        help="Часовой пояс для --start (по умолчанию OUTLOOK_TIMEZONE)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    args = build_parser().parse_args(argv)
    config = load_config()
    attendees = [email.strip() for email in args.to if email.strip()]
    resources = [email.strip() for email in [*args.resource, *args.room] if email.strip()]

    if args.no_end:
        end_type: RecurrenceEndType = "no_end"
        occurrences = None
        end_value = None
    elif args.end:
        end_type = "end_date"
        occurrences = None
        end_value = args.end
    else:
        end_type = "occurrences"
        occurrences = args.occurrences
        end_value = None

    weekdays = args.weekday or None
    try:
        result = dispatch_recurring_meeting_invite(
            attendee=attendees[0],
            attendees=attendees,
            subject=args.subject,
            start=args.start,
            duration_minutes=args.duration,
            pattern=args.pattern,
            interval=args.interval,
            weekdays=weekdays,
            day_of_month=args.day_of_month,
            end_type=end_type,
            occurrences=occurrences,
            end=end_value,
            body=args.body,
            location=args.location,
            resources=resources,
            timezone=args.tz,
            config=config,
        )
    except Exception as error:
        print(f"Ошибка: {error}", file=sys.stderr)
        return 1

    if result.get("warning"):
        print(f"WARN: {result['warning']}", file=sys.stderr)

    print(f"Повторяющееся приглашение отправлено: {result['subject']}")
    print(f"  От: {result['from']}")
    print(f"  Кому: {', '.join(result['attendees'])}")
    print(f"  Первое: {result['start']}")
    print(f"  Длительность: {result['duration_minutes']} мин.")
    print(f"  Повторение: {result['recurrence']}")
    if result["resources"]:
        print("  Переговорные:")
        for email in result["resources"]:
            print(f"    - {email}")
    if result.get("outlook_meeting_url"):
        print(f"  Ссылка в Outlook: {result['outlook_meeting_url']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
