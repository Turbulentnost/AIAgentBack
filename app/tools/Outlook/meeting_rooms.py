"""
Переговорные комнаты Exchange: загрузка списка и проверка занятости (Free/Busy).

Примеры:
  python -m app.tools.Outlook.meeting_rooms --list
  python -m app.tools.Outlook.meeting_rooms --discover --list
  python -m app.tools.Outlook.meeting_rooms --check --start "2026-06-10 14:00" --duration 60
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from app.tools.Outlook.cancel_meeting import to_ews, to_local
from app.tools.Outlook.outlook_config import OutlookConfig
from app.tools.Outlook.send_meeting_invite import connect_account, load_config, parse_start

DEFAULT_ROOMS_FILE = Path(__file__).resolve().parent / "meeting_rooms.json"
DISCOVER_QUERIES = (
    "кабинет",
    "Кабинет",
    "dir",
    "zam",
    "pred",
    "komdir",
    "techdir",
    "findir",
    "ispoln",
    "serv",
    "proizv",
    "cheh",
    "цех",
    "konf",
    "zal",
    "зал",
    "конференц",
    "переговор",
    "room",
    "mgs",
)
KNOWN_ROOM_EMAILS = {
    "konfzalkb@turbo-don.ru",
    "npo_konf@turbo-don.ru",
    "dir_mgs@turbo-don.ru",
    "dirservobslig@turbo-don.ru",
    "zamkomdir@turbo-don.ru",
    "ispolndir@turbo-don.ru",
    "komdir@turbo-don.ru",
    "predsovdir@turbo-don.ru",
    "techdirnpo@turbo-don.ru",
    "findirnpo@turbo-don.ru",
    "proizvodcheh1@turbo-don.ru",
}
ROOM_EMAIL_MARKERS = (
    "dir_mgs",
    "dirservobslig",
    "zamkomdir",
    "ispolndir",
    "komdir",
    "predsovdir",
    "techdir",
    "findir",
    "proizvodcheh",
    "konfzalkb",
    "npo_konf",
)
BUSY_STATUSES = frozenset({"Busy", "Tentative", "OOF", "WorkingElsewhere", "NoData"})


def intervals_overlap(a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime) -> bool:
    return a_start < b_end and a_end > b_start


def load_rooms_payload(path: Path | str | None = None) -> dict[str, Any]:
    rooms_file = Path(path) if path else DEFAULT_ROOMS_FILE
    if not rooms_file.is_file():
        return {"rooms": [], "pending_without_email": []}
    return json.loads(rooms_file.read_text(encoding="utf-8"))


def load_rooms(path: Path | str | None = None) -> list[dict[str, str]]:
    payload = load_rooms_payload(path)
    rooms: list[dict[str, str]] = []
    for item in payload.get("rooms", []):
        email = (item.get("email") or "").strip()
        name = (item.get("name") or email).strip()
        if email:
            rooms.append({"name": name, "email": email})
    return rooms


def load_pending_room_names(path: Path | str | None = None) -> list[str]:
    payload = load_rooms_payload(path)
    return [str(name).strip() for name in payload.get("pending_without_email", []) if str(name).strip()]


def normalize_room_name(value: str) -> str:
    text = (value or "").strip().lower().replace("ё", "е")
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"[«»\"'.,;:!?()\[\]{}]", "", text)
    return text


def resolve_room_by_name(
    room_name: str,
    *,
    rooms_file: Path | str | None = None,
    min_score: float = 0.85,
) -> dict[str, str] | None:
    """Ищет переговорную в meeting_rooms.json по названию помещения."""
    query = normalize_room_name(room_name)
    if not query:
        return None

    best_match: dict[str, str] | None = None
    best_score = 0.0
    for room in load_rooms(rooms_file):
        score = SequenceMatcher(None, query, normalize_room_name(room["name"])).ratio()
        if score > best_score:
            best_score = score
            best_match = room

    if best_match and best_score >= min_score:
        return best_match
    return None


def discover_rooms_from_ews(*, config: OutlookConfig | None = None) -> list[dict[str, str]]:
    """Попытка получить комнаты через GetRoomLists и ResolveNames."""
    config = config or load_config()
    account = connect_account(config)
    found: dict[str, dict[str, str]] = {}

    for roomlist in account.protocol.get_roomlists():
        email = (getattr(roomlist, "email_address", "") or "").strip()
        if email:
            for room in account.protocol.get_rooms(email):
                room_email = (getattr(room, "email_address", "") or "").strip()
                room_name = (getattr(room, "name", "") or room_email).strip()
                if room_email:
                    found[room_email.lower()] = {"name": room_name, "email": room_email}

    for query in DISCOVER_QUERIES:
        try:
            matches = account.protocol.resolve_names([query], return_full_contact_data=True)
        except Exception:
            continue
        for item in matches:
            mailbox = item[0] if isinstance(item, tuple) else item
            email = (getattr(mailbox, "email_address", "") or "").strip()
            name = (getattr(mailbox, "name", "") or email).strip()
            if not email:
                continue
            if looks_like_room(email, name):
                found[email.lower()] = {"name": name, "email": email}

    return sorted(found.values(), key=lambda row: row["name"].lower())


def looks_like_room(email: str, name: str) -> bool:
    address = email.lower().strip()
    title = name.lower().strip()
    if address in KNOWN_ROOM_EMAILS:
        return True
    if any(marker in address for marker in ROOM_EMAIL_MARKERS):
        return True
    if any(token in address for token in ("konf", "room", "hall", "conf", "konfz", "proizvodcheh")):
        return True
    if title.startswith("кабинет "):
        return True
    if any(token in title for token in ("конферен", "переговор", "зал совещ", "зал кб", "производственный цех")):
        return True
    return False


def merge_rooms(*sources: list[dict[str, str]]) -> list[dict[str, str]]:
    merged: dict[str, dict[str, str]] = {}
    for rows in sources:
        for row in rows:
            email = row["email"].strip()
            merged[email.lower()] = {"name": row["name"], "email": email}
    return sorted(merged.values(), key=lambda row: row["name"].lower())


def parse_freebusy_events(
    events: list[Any],
    *,
    config: OutlookConfig,
) -> list[tuple[datetime, datetime]]:
    intervals: list[tuple[datetime, datetime]] = []
    for event in events:
        status = str(getattr(event, "busy_type", "") or "")
        if status == "Free" or (status and status not in BUSY_STATUSES):
            continue
        start = to_local(event.start, config)
        end = to_local(event.end, config)
        if end > start:
            intervals.append((start, end))
    return intervals


def is_interval_free(
    slot_start: datetime,
    slot_end: datetime,
    busy_intervals: list[tuple[datetime, datetime]],
) -> bool:
    for busy_start, busy_end in busy_intervals:
        if intervals_overlap(slot_start, slot_end, busy_start, busy_end):
            return False
    return True


def check_rooms_status(
    *,
    config: OutlookConfig,
    rooms: list[dict[str, str]],
    slot_start: datetime,
    slot_end: datetime,
) -> list[dict[str, Any]]:
    if not rooms:
        return []

    slot_start = to_local(slot_start, config)
    slot_end = to_local(slot_end, config)
    account = connect_account(config)
    mailbox_data = [(room["email"], "Resource", False) for room in rooms]
    views = list(
        account.protocol.get_free_busy_info(
            mailbox_data,
            start=to_ews(slot_start, config),
            end=to_ews(slot_end, config),
            requested_view="DetailedMerged",
        )
    )
    if len(views) != len(rooms):
        raise RuntimeError(
            f"Free/busy вернул {len(views)} ответов для {len(rooms)} переговорных"
        )

    result: list[dict[str, Any]] = []
    for room, view in zip(rooms, views):
        events = list(view.calendar_events or [])
        busy_intervals = parse_freebusy_events(events, config=config)
        free = is_interval_free(slot_start, slot_end, busy_intervals)
        result.append(
            {
                "name": room["name"],
                "email": room["email"],
                "status": "free" if free else "busy",
                "status_label": "свободна" if free else "занята",
                "busy_events": len(busy_intervals),
            }
        )
    return result


def format_rooms_status(
    rooms_status: list[dict[str, Any]],
    *,
    slot_start: datetime,
    slot_end: datetime,
) -> str:
    if not rooms_status:
        return "Переговорные: список пуст (заполните meeting_rooms.json)."

    lines = [
        f"Переговорные на {slot_start.strftime('%d.%m.%Y %H:%M')} — "
        f"{slot_end.strftime('%H:%M')}:",
    ]
    free_count = sum(1 for row in rooms_status if row["status"] == "free")
    lines.append(f"  свободно: {free_count}/{len(rooms_status)}")
    for row in rooms_status:
        lines.append(
            f"  [{row['status_label']:7}] {row['name']} ({row['email']})"
        )
    return "\n".join(lines)


def resolve_rooms_list(
    *,
    rooms_file: str | None = None,
    discover: bool = False,
    config: OutlookConfig | None = None,
) -> tuple[list[dict[str, str]], list[dict[str, str]], list[dict[str, str]]]:
    config = config or load_config()
    configured = load_rooms(rooms_file)
    discovered: list[dict[str, str]] = []
    if discover:
        discovered = discover_rooms_from_ews(config=config)
        rooms = merge_rooms(configured, discovered)
    else:
        rooms = configured
    return rooms, configured, discovered


def dispatch_meeting_rooms(
    *,
    list_only: bool = True,
    check: bool = False,
    discover: bool = False,
    rooms_file: str | None = None,
    start: str = "",
    duration_minutes: int = 60,
    timezone: str | None = None,
    config: OutlookConfig | None = None,
) -> dict[str, Any]:
    """Возвращает список переговорных и/или их занятость для API/агента."""
    config = config or load_config()
    rooms, configured, discovered = resolve_rooms_list(
        rooms_file=rooms_file,
        discover=discover,
        config=config,
    )

    payload: dict[str, Any] = {
        "rooms": rooms,
        "rooms_count": len(rooms),
        "pending_without_email": load_pending_room_names(rooms_file),
    }

    configured_emails = {row["email"].lower() for row in configured}
    payload["discovered_not_in_json"] = [
        row for row in discovered if row["email"].lower() not in configured_emails
    ]

    if check:
        if not start.strip():
            raise ValueError("Для check=true укажите start.")
        if not rooms:
            raise ValueError("Список переговорных пуст.")
        tz_name = timezone or config.timezone
        slot_start = parse_start(start, tz_name)
        slot_end = slot_start + timedelta(minutes=max(duration_minutes, 1))
        payload["slot_start"] = slot_start.isoformat()
        payload["slot_end"] = slot_end.isoformat()
        payload["rooms_status"] = check_rooms_status(
            config=config,
            rooms=rooms,
            slot_start=slot_start,
            slot_end=slot_end,
        )
        payload["free_count"] = sum(
            1 for row in payload["rooms_status"] if row["status"] == "free"
        )
    elif not list_only:
        raise ValueError("Укажите list_only=true или check=true.")

    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Список переговорных и их занятость.")
    parser.add_argument(
        "--rooms-file",
        default=str(DEFAULT_ROOMS_FILE),
        help=f"JSON со списком комнат (по умолчанию {DEFAULT_ROOMS_FILE.name})",
    )
    parser.add_argument(
        "--discover",
        action="store_true",
        help="Дополнить список комнатами из Exchange (GetRoomLists / ResolveNames)",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="Показать список переговорных",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Проверить занятость всех комнат на указанный слот",
    )
    parser.add_argument(
        "--start",
        help="Начало слота: YYYY-MM-DD HH:MM",
    )
    parser.add_argument(
        "--duration",
        type=int,
        default=60,
        help="Длительность слота в минутах (для --check)",
    )
    parser.add_argument(
        "--tz",
        default=None,
        help="Часовой пояс для --start (по умолчанию OUTLOOK_TIMEZONE из .env)",
    )
    parser.add_argument(
        "-o",
        "--output",
        help="Сохранить JSON",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    args = build_parser().parse_args(argv)
    config = load_config()

    if not args.list and not args.check:
        args.list = True

    try:
        payload = dispatch_meeting_rooms(
            list_only=not args.check or args.list,
            check=args.check,
            discover=args.discover,
            rooms_file=args.rooms_file.strip() or None,
            start=args.start or "",
            duration_minutes=args.duration,
            timezone=args.tz,
            config=config,
        )
    except Exception as error:
        print(f"Ошибка: {error}", file=sys.stderr)
        return 1

    if args.list:
        pending = payload.get("pending_without_email") or []
        print(f"Переговорных: {payload['rooms_count']}")
        for room in payload["rooms"]:
            print(f"  - {room['name']} ({room['email']})")
        if pending:
            print(f"\nБез e-mail в Exchange (нужно уточнить у админа): {len(pending)}")
            for name in pending:
                print(f"  - {name}")
        new_from_discover = payload.get("discovered_not_in_json") or []
        if new_from_discover:
            print(f"\nНайдено через Exchange, но ещё не в JSON: {len(new_from_discover)}")
            for room in new_from_discover:
                print(f"  + {room['name']} ({room['email']})")

    if args.check and payload.get("rooms_status"):
        slot_start = datetime.fromisoformat(payload["slot_start"])
        slot_end = datetime.fromisoformat(payload["slot_end"])
        print()
        print(format_rooms_status(payload["rooms_status"], slot_start=slot_start, slot_end=slot_end))

    if args.output:
        Path(args.output).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"\nСохранено: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
