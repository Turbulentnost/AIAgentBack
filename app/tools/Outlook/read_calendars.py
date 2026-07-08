"""
Чтение календарей через EWS (exchangelib) под учёткой из outlook_config.

Outlook может показывать расшаренные календари, если:
  - у Postagent есть mailbox в Exchange, или
  - указан владелец календаря (--owner), и права выданы на Postagent.

Примеры:
  python -m app.tools.Outlook.read_calendars --list
  python -m app.tools.Outlook.read_calendars --owner ivanov@turbo-don.ru --days 14
  python -m app.tools.Outlook.read_calendars --owner a@turbo-don.ru --owner b@turbo-don.ru --days 7 -o events.json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from exchangelib import DELEGATE, EWSDateTime, EWSTimeZone, Account, Configuration, Credentials
from exchangelib.errors import ErrorFolderNotFound, ErrorNonExistentMailbox
from exchangelib.folders import Calendar, Folder
from exchangelib.version import EXCHANGE_2013_SP1, Version

from app.tools.Outlook.cancel_meeting import to_ews
from app.tools.Outlook.outlook_config import OutlookConfig
from app.tools.Outlook.send_meeting_invite import connect_account, load_config, primary_smtp_address


def ews_timezone(config: OutlookConfig) -> EWSTimeZone:
    return EWSTimeZone.from_timezone(ZoneInfo(config.timezone))


def date_range(*, days: int, config: OutlookConfig) -> tuple[EWSDateTime, EWSDateTime]:
    tz = ews_timezone(config)
    start = EWSDateTime.now(tz=tz).replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=max(days, 1))
    return start, end


_EWS_VERSION = Version(build=EXCHANGE_2013_SP1)


def ews_configuration(config: OutlookConfig) -> Configuration:
    credentials = Credentials(username=config.email, password=config.password)
    if config.server:
        return Configuration(
            server=config.server,
            credentials=credentials,
            version=_EWS_VERSION,
        )
    return Configuration(
        credentials=credentials,
        version=_EWS_VERSION,
    )


def connect_as_owner(config: OutlookConfig, owner_smtp: str) -> Account:
    """Календарь другого пользователя: логин Postagent, mailbox = owner."""
    if config.server:
        return Account(
            primary_smtp_address=owner_smtp.strip(),
            config=ews_configuration(config),
            autodiscover=False,
            access_type=DELEGATE,
        )
    return Account(
        primary_smtp_address=owner_smtp.strip(),
        credentials=Credentials(username=config.email, password=config.password),
        autodiscover=True,
        access_type=DELEGATE,
    )


def folder_label(folder: Folder) -> str:
    parts = [getattr(folder, "name", "") or "(без имени)"]
    mailbox = getattr(folder, "mailbox", None)
    if mailbox and getattr(mailbox, "email_address", None):
        parts.append(str(mailbox.email_address))
    return " — ".join(parts)


def list_calendar_folders(account: Account) -> list[dict[str, Any]]:
    """Все папки типа Calendar, видимые через EWS."""
    found: list[dict[str, Any]] = []
    for folder in account.root.walk():
        if isinstance(folder, Calendar):
            found.append(
                {
                    "name": folder.name,
                    "folder_class": folder.__class__.__name__,
                    "id": folder.id,
                    "mailbox": str(getattr(folder, "mailbox", "") or ""),
                }
            )
    return found


def event_to_dict(item: Any) -> dict[str, Any]:
    organizer = None
    if item.organizer:
        organizer = getattr(item.organizer, "email_address", None) or str(item.organizer)
    return {
        "subject": item.subject or "",
        "start": str(item.start),
        "end": str(item.end),
        "location": item.location or "",
        "organizer": organizer,
        "is_cancelled": bool(item.is_cancelled),
        "legacy_free_busy_status": str(item.legacy_free_busy_status or ""),
    }


def read_calendar_items_in_range(
    config: OutlookConfig,
    owner_smtp: str,
    *,
    range_start: datetime,
    range_end: datetime,
    max_items: int = 50,
) -> list[Any]:
    """События календаря владельца в интервале (EWS Delegate через Postagent)."""
    if range_end <= range_start:
        return []
    account = connect_as_owner(config, owner_smtp)
    try:
        calendar = account.calendar
    except ErrorFolderNotFound as error:
        raise RuntimeError(
            "Папка календаря не найдена. Проверьте права Reviewer/Delegate "
            "или укажите другой --owner."
        ) from error

    start = to_ews(range_start, config)
    end = to_ews(range_end, config)
    return list(calendar.view(start=start, end=end, max_items=max_items))


def read_events_in_range(
    account: Account,
    *,
    range_start: datetime,
    range_end: datetime,
    max_items: int,
    config: OutlookConfig,
) -> list[dict[str, Any]]:
    if range_end <= range_start:
        return []
    start = to_ews(range_start, config)
    end = to_ews(range_end, config)
    try:
        calendar = account.calendar
    except ErrorFolderNotFound as error:
        raise RuntimeError(
            "Папка календаря не найдена. Проверьте права Reviewer/Delegate "
            "или укажите другой --owner."
        ) from error

    items = calendar.view(start=start, end=end, max_items=max_items)
    return [event_to_dict(item) for item in items]


def read_events(
    account: Account,
    *,
    days: int,
    max_items: int,
    config: OutlookConfig,
) -> list[dict[str, Any]]:
    start, end = date_range(days=days, config=config)
    try:
        calendar = account.calendar
    except ErrorFolderNotFound as error:
        raise RuntimeError(
            "Папка календаря не найдена. Проверьте права Reviewer/Delegate "
            "или укажите другой --owner."
        ) from error

    items = calendar.view(start=start, end=end, max_items=max_items)
    return [event_to_dict(item) for item in items]


def read_owner_calendar(
    config: OutlookConfig,
    owner_smtp: str,
    *,
    days: int,
    max_items: int,
) -> dict[str, Any]:
    account = connect_as_owner(config, owner_smtp)
    events = read_events(account, days=days, max_items=max_items, config=config)
    return {
        "owner": owner_smtp.strip(),
        "login_as": config.email,
        "events_count": len(events),
        "events": events,
    }


def list_all_calendars(config: OutlookConfig) -> dict[str, Any]:
    result: dict[str, Any] = {
        "login": config.email,
        "mailbox_smtp": primary_smtp_address(config),
        "own_mailbox_ok": False,
        "own_calendars": [],
        "note": "",
    }

    try:
        account = connect_account(config, verify_mailbox=False)
        _ = account.calendar
        result["own_mailbox_ok"] = True
        result["own_calendars"] = list_calendar_folders(account)
    except ErrorNonExistentMailbox:
        result["note"] = (
            "У Postagent нет mailbox в Exchange (EWS). "
            "Календари из Outlook Desktop могут быть видны в UI, "
            "но код их не прочитает, пока не появится mailbox или "
            "не укажете --owner с email владельца каждого календаря."
        )
    except ErrorFolderNotFound:
        result["own_mailbox_ok"] = True
        result["note"] = "Mailbox есть, но стандартный Calendar не найден."
        account = connect_account(config, verify_mailbox=False)
        result["own_calendars"] = list_calendar_folders(account)
    except Exception as error:
        result["note"] = f"Ошибка: {error}"

    return result


def fetch_outlook_calendars(
    *,
    list_own: bool = False,
    owners: list[str] | None = None,
    days: int = 14,
    max_items: int = 100,
    config: OutlookConfig | None = None,
) -> dict[str, Any]:
    """Читает календари через EWS и возвращает JSON-структуру для API/агента."""
    config = config or load_config()
    owner_list = [owner.strip() for owner in (owners or []) if owner.strip()]

    if not list_own and not owner_list:
        raise ValueError(
            "Укажите list_own=true или owners с email владельцев календарей"
        )

    payload: dict[str, Any] = {
        "login": config.email,
        "generated_at": datetime.now(ZoneInfo(config.timezone)).isoformat(),
    }

    if list_own:
        payload["calendars"] = list_all_calendars(config)

    if owner_list:
        payload["shared_calendars"] = [
            read_owner_calendar(
                config,
                owner,
                days=days,
                max_items=max_items,
            )
            for owner in owner_list
        ]

    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Чтение календарей Exchange (EWS)")
    parser.add_argument(
        "--list",
        action="store_true",
        help="Показать календари в mailbox Postagent (если EWS видит ящик)",
    )
    parser.add_argument(
        "--owner",
        action="append",
        default=[],
        metavar="EMAIL",
        help="SMTP владельца расшаренного календаря (можно несколько раз)",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=14,
        help="Сколько дней вперёд читать (по умолчанию 14)",
    )
    parser.add_argument(
        "--max-items",
        type=int,
        default=100,
        help="Максимум событий на один календарь",
    )
    parser.add_argument(
        "-o",
        "--output",
        help="Сохранить JSON в файл",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    args = build_parser().parse_args(argv)

    if not args.list and not args.owner:
        print(
            "Укажите --list или --owner user@turbo-don.ru\n"
            "Подсказка: в Outlook ПКМ по календарю → «Свойства» / sharing — "
            "там виден владелец (email).",
            file=sys.stderr,
        )
        return 2

    try:
        payload = fetch_outlook_calendars(
            list_own=args.list,
            owners=args.owner,
            days=args.days,
            max_items=args.max_items,
        )
    except ValueError as error:
        print(f"Ошибка: {error}", file=sys.stderr)
        return 2
    except Exception as error:
        print(f"Ошибка: {error}", file=sys.stderr)
        return 1

    text = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as file:
            file.write(text)
        print(f"Сохранено: {args.output}")
    else:
        print(text)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
