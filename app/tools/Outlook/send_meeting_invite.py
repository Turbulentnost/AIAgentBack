"""
Отправка приглашения на совещание другому пользователю от имени учётной записи из outlook_config.

Примеры:
  python -m app.tools.Outlook.send_meeting_invite --to user@company.ru --subject "Совещание" --start "2026-06-05 14:00"
  python -m app.tools.Outlook.send_meeting_invite --to a@co.ru --to b@co.ru -s "Совещание" --start "2026-06-05 14:00" --resource room@co.ru
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from exchangelib import DELEGATE, Account, CalendarItem, Configuration, Credentials, Mailbox
from exchangelib.errors import ErrorNonExistentMailbox
from exchangelib.items import SEND_ONLY_TO_ALL
from exchangelib.properties import Attendee
from exchangelib.version import EXCHANGE_2013_SP1, Version

from app.tools.Outlook.outlook_config import OutlookConfig, build_outlook_config
from app.tools.Outlook.outlook_html_body import plain_text_to_html
from app.tools.Outlook.outlook_meeting_link import calendar_item_outlook_meta


_EWS_VERSION = Version(build=EXCHANGE_2013_SP1)


def load_config() -> OutlookConfig:
    return build_outlook_config()


def primary_smtp_address(config: OutlookConfig) -> str:
    if config.mailbox:
        return config.mailbox.strip()
    return config.email.strip()


def is_shared_mailbox(config: OutlookConfig) -> bool:
    return bool(config.mailbox) and config.mailbox.strip().lower() != config.email.strip().lower()


def connect_account(config: OutlookConfig, *, verify_mailbox: bool = True) -> Account:
    if not config.email or not config.password:
        raise ValueError(
            "Не заданы email или password. Заполните .env: "
            "OUTLOOK_EMAIL / OUTLOOK_PASSWORD."
        )
    if not primary_smtp_address(config):
        raise ValueError("Не задан email (и OUTLOOK_MAILBOX, если SMTP ящика другой).")

    smtp = primary_smtp_address(config)
    credentials = Credentials(username=config.email, password=config.password)
    if config.server:
        configuration = Configuration(
            server=config.server,
            credentials=credentials,
            version=_EWS_VERSION,
        )
        account = Account(
            primary_smtp_address=smtp,
            config=configuration,
            autodiscover=False,
            access_type=DELEGATE,
        )
    else:
        account = Account(
            primary_smtp_address=smtp,
            credentials=credentials,
            autodiscover=True,
            access_type=DELEGATE,
        )

    if verify_mailbox:
        verify_mailbox_access(account, config)
    return account


def verify_mailbox_access(account: Account, config: OutlookConfig) -> None:
    try:
        _ = account.calendar
    except ErrorNonExistentMailbox as error:
        raise ValueError(
            "Exchange не находит почтовый ящик с таким SMTP (календарь недоступен).\n"
            f"  Логин: {config.email}\n"
            f"  SMTP ящика: {primary_smtp_address(config)}\n\n"
            "Пароль может быть верным, но EWS ищет ящик по SMTP. "
            "Уточните у админа основной адрес ящика Postagent в Exchange "
            "или включение EWS для этой учётки."
        ) from error
    except Exception as error:
        if "не сопоставлен" in str(error).lower():
            raise ValueError(
                f"Не удалось открыть календарь ящика {primary_smtp_address(config)}: {error}"
            ) from error
        raise


def resolve_attendee(address: str) -> Attendee:
    address = address.strip()
    return Attendee(mailbox=Mailbox(email_address=address))


def parse_start(value: str, tz_name: str) -> datetime:
    normalized = value.strip()
    if not normalized:
        raise ValueError("Пустая дата начала совещания")

    iso_candidate = normalized.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(iso_candidate)
        if parsed.tzinfo is not None:
            return parsed.astimezone(ZoneInfo(tz_name))
        return parsed.replace(tzinfo=ZoneInfo(tz_name))
    except ValueError:
        pass

    tz = ZoneInfo(tz_name)
    for fmt in (
        "%Y-%m-%d %H:%M",
        "%Y-%m-%dT%H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%d.%m.%Y %H:%M",
        "%d.%m.%Y %H:%M:%S",
    ):
        try:
            naive = datetime.strptime(normalized, fmt)
            return naive.replace(tzinfo=tz)
        except ValueError:
            continue
    raise ValueError(
        f"Не удалось разобрать дату «{value}». "
        "Форматы: ISO 8601 (2026-06-22T08:00:00+03:00), YYYY-MM-DD HH:MM, "
        "YYYY-MM-DDTHH:MM, DD.MM.YYYY HH:MM"
    )


def resolve_resource(address: str) -> Attendee:
    return Attendee(mailbox=Mailbox(email_address=address.strip()))


def send_meeting_invite(
    *,
    config: OutlookConfig,
    attendee: str,
    subject: str,
    start: datetime,
    duration_minutes: int,
    body: str = "",
    location: str = "",
    resources: list[str] | None = None,
    attendees: list[str] | None = None,
) -> CalendarItem:
    account = connect_account(config)
    end = start + timedelta(minutes=duration_minutes)
    people = attendees or [attendee]
    room_resources = [email.strip() for email in (resources or []) if email.strip()]

    invite_body = plain_text_to_html(body or subject)
    item = CalendarItem(
        account=account,
        folder=account.calendar,
        subject=subject,
        body=invite_body,
        start=start,
        end=end,
        location=location,
        required_attendees=[resolve_attendee(person) for person in people],
        resources=[resolve_resource(room) for room in room_resources],
    )
    item.save(send_meeting_invitations=SEND_ONLY_TO_ALL)
    return item


def dispatch_meeting_invite(
    *,
    attendee: str,
    subject: str,
    start: str,
    duration_minutes: int = 60,
    body: str = "",
    location: str = "",
    resources: list[str] | None = None,
    attendees: list[str] | None = None,
    timezone: str | None = None,
    config: OutlookConfig | None = None,
) -> dict[str, Any]:
    """Отправляет приглашение и возвращает JSON-структуру для API/агента."""
    config = config or load_config()
    tz_name = timezone or config.timezone
    start_dt = parse_start(start, tz_name)
    people = [person.strip() for person in (attendees or [attendee]) if person.strip()]
    if not people:
        raise ValueError("Не указан ни один участник (attendee / attendees).")

    room_resources = [email.strip() for email in (resources or []) if email.strip()]
    item = send_meeting_invite(
        config=config,
        attendee=people[0],
        subject=subject,
        start=start_dt,
        duration_minutes=duration_minutes,
        body=body,
        location=location,
        resources=room_resources,
        attendees=people,
    )
    end_dt = start_dt + timedelta(minutes=duration_minutes)
    outlook_meta = calendar_item_outlook_meta(item, config)
    from app.tools.Outlook.company_calendar_sync import sync_meeting_to_company_calendar

    company_meta = sync_meeting_to_company_calendar(item, config=config)
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
        **outlook_meta,
        **company_meta,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Отправить приглашение на совещание через Exchange."
    )
    parser.add_argument(
        "--to",
        action="append",
        required=True,
        metavar="EMAIL",
        help="E-mail приглашаемого (можно несколько раз)",
    )
    parser.add_argument(
        "--subject",
        "-s",
        required=True,
        help="Тема совещания",
    )
    parser.add_argument(
        "--start",
        required=True,
        help="Начало (локальное время): YYYY-MM-DD HH:MM или DD.MM.YYYY HH:MM",
    )
    parser.add_argument(
        "--duration",
        type=int,
        default=60,
        metavar="MIN",
        help="Длительность в минутах (по умолчанию 60)",
    )
    parser.add_argument(
        "--body",
        "-b",
        default="",
        help="Текст приглашения",
    )
    parser.add_argument(
        "--location",
        "-l",
        default="",
        help="Место проведения (текст в приглашении)",
    )
    parser.add_argument(
        "--resource",
        action="append",
        default=[],
        metavar="EMAIL",
        help="E-mail переговорной (ресурс Exchange). Можно указать несколько раз.",
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
        help="Часовой пояс для --start (по умолчанию OUTLOOK_TIMEZONE из .env)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    args = build_parser().parse_args(argv)
    config = load_config()
    attendees = [email.strip() for email in args.to if email.strip()]
    resources = [email.strip() for email in [*args.resource, *args.room] if email.strip()]

    try:
        result = dispatch_meeting_invite(
            attendee=attendees[0],
            attendees=attendees,
            subject=args.subject,
            start=args.start,
            duration_minutes=args.duration,
            body=args.body,
            location=args.location,
            resources=resources,
            timezone=args.tz,
            config=config,
        )
    except Exception as error:
        print(f"Ошибка: {error}", file=sys.stderr)
        return 1

    print(f"Приглашение отправлено: {result['subject']}")
    print(f"  От: {result['from']}")
    print(f"  Кому: {', '.join(result['attendees'])}")
    print(f"  Начало: {result['start']}")
    print(f"  Длительность: {result['duration_minutes']} мин.")
    if result["resources"]:
        print("  Переговорные (ресурсы):")
        for email in result["resources"]:
            print(f"    - {email}")
    if result.get("outlook_meeting_url"):
        print(f"  Ссылка в Outlook: {result['outlook_meeting_url']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
