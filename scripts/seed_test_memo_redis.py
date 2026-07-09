"""Создаёт тестовую СЗ в Redis (memo + dashboard), без 1С."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from datetime import date, datetime, timezone

from app.agents.meeting_agent.dashboard import merge_dashboard_items
from app.agents.meeting_agent.memo_presenter import build_queue_item_from_row
from app.services.meeting_dashboard_cache import MeetingDashboardCacheService
from app.services.meeting_memo_cache import MeetingMemoCacheService, build_detail_from_dashboard_item
from app.tools.onec.get_meetings import meeting_theme
from app.tools.onec.lookup_email_by_fio import lookup_email_by_fio

REF_NAMESPACE = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")

DEFAULT_NUMBER = "000000000"
DEFAULT_PARTICIPANTS = (
    "Мангасарян Давид Каренович",
    "Комарькова Анастасия Эдуардовна",
)
DEFAULT_MEETING_DATE = "2026-07-14"
DEFAULT_START = "13:00"
DEFAULT_END = "14:00"
DEFAULT_ROOM = "Зал совещаний КБ"
DEFAULT_SUBJECT = "Тестовая СЗ: проверка агента совещаний (14.07.2026)"


def _ref_key_for_number(number: str) -> str:
    return str(uuid.uuid5(REF_NAMESPACE, f"meeting-memo:{number.strip()}"))


def _lookup_email(fio: str) -> str | None:
    try:
        result = lookup_email_by_fio(fio)
        emails = result.get("emails") or []
        if emails and emails[0].get("email"):
            return str(emails[0]["email"])
    except Exception:
        return None
    return None


def _person(full_name: str, *, email: str | None = None) -> dict:
    return {
        "full_name": full_name,
        "email": email,
        "ref_key": None,
        "department": None,
        "position": None,
    }


def build_queue_item(
    *,
    ref_key: str,
    number: str,
    participants: tuple[str, ...],
    meeting_date: str,
    start_time: str,
    end_time: str,
    room: str,
    subject: str,
    document_day: date,
) -> dict:
    theme = meeting_theme() or "Организация совещаний (регл.)"
    header = {
        "Ref_Key": ref_key,
        "Number": number,
        "Date": f"{document_day.isoformat()}T10:00:00",
        "Статус": "НеСогласована",
        "ТемаСлужебнойЗаписки": theme,
        "ТемаСовещания": subject,
        "ЦельПланаСовещания": "Тестовая проверка сценария планирования и согласования совещания.",
        "ЖелаемаяДатаПроведенияСовещания": f"{meeting_date}T00:00:00",
        "ДатаПроведенияСовещания": f"{meeting_date}T00:00:00",
        "ВремяНачалаСовещания": f"{meeting_date}T{start_time}:00",
        "ВремяОкончанияСовещания": f"{meeting_date}T{end_time}:00",
        "МестоПроведенияСовещания": room,
        "ВидСовещания": "Внеплановое",
        "ПланСовещания": [
            {"LineNumber": 1, "Задача": "Проверить подбор слота в календаре"},
            {"LineNumber": 2, "Задача": "Проверить отправку приглашения"},
        ],
        "СписокУчастников": [
            {"LineNumber": index + 1, "Участник": name}
            for index, name in enumerate(participants)
        ],
    }
    manager_name = participants[-1]
    initiator_name = participants[0]
    item = build_queue_item_from_row(header)
    item.update(
        {
            "title": subject,
            "subject": subject,
            "location": room,
            "participant_names": list(participants),
            "participants_count": len(participants),
            "initiator": _person(initiator_name),
            "manager": _person(manager_name),
            "meeting_start": f"{meeting_date}T{start_time}:00",
            "meeting_end": f"{meeting_date}T{end_time}:00",
            "ПланСовещания": header["ПланСовещания"],
            "СписокУчастников": header["СписокУчастников"],
            "ЦельПланаСовещания": header["ЦельПланаСовещания"],
        }
    )
    return item


def build_detail(queue_item: dict, participants: tuple[str, ...]) -> dict:
    detail = build_detail_from_dashboard_item(queue_item)
    manager_name = participants[-1]
    manager_email = _lookup_email(manager_name)
    detail["application"]["participants"] = [
        _person(name, email=_lookup_email(name)) for name in participants
    ]
    detail["application"]["manager"] = _person(manager_name, email=manager_email)
    detail["application"]["initiator"] = _person(
        participants[0],
        email=_lookup_email(participants[0]) or manager_email,
    )
    detail["application"]["participants_count"] = len(participants)
    detail["application"]["agenda"] = queue_item.get("subject")
    detail["application"]["duration_minutes"] = 60
    detail["cache_source"] = "redis"
    detail["history"] = [
        {
            "at": datetime.now(timezone.utc).isoformat(),
            "message": "Тестовая СЗ создана в Redis (seed)",
        }
    ]
    return detail


async def seed_redis(
    *,
    ref_key: str,
    number: str,
    participants: tuple[str, ...],
    meeting_date: str,
    start_time: str,
    end_time: str,
    room: str,
    subject: str,
    dashboard_day: date | None,
) -> dict:
    day = dashboard_day or date.today()
    queue_item = build_queue_item(
        ref_key=ref_key,
        number=number,
        participants=participants,
        meeting_date=meeting_date,
        start_time=start_time,
        end_time=end_time,
        room=room,
        subject=subject,
        document_day=day,
    )
    detail = build_detail(queue_item, participants)

    fetched_at = datetime.now(timezone.utc)
    memo_service = MeetingMemoCacheService()
    dashboard_service = MeetingDashboardCacheService()
    await memo_service._write_cache(ref_key, detail, fetched_at=fetched_at)

    cached = await dashboard_service._read_cache(day)
    if cached is not None:
        payload = {
            key: value
            for key, value in cached.items()
            if key not in {"fetched_at", "fetch_ok"}
        }
        unapproved = [
            item
            for item in (payload.get("unapproved") or [])
            if (item.get("ref_key") or "").lower() != ref_key.lower()
        ]
        today = [
            item
            for item in (payload.get("today") or [])
            if (item.get("ref_key") or "").lower() != ref_key.lower()
        ]
    else:
        unapproved = []
        today = []

    unapproved.append(queue_item)
    today.append(queue_item)
    items = merge_dashboard_items(unapproved, today)

    await dashboard_service._write_cache(
        day,
        {
            "date": day.isoformat(),
            "unapproved": unapproved,
            "today": today,
            "items": items,
            "counts": {
                "unapproved": len(unapproved),
                "today": len(today),
                "items": len(items),
            },
        },
        fetched_at=fetched_at,
    )

    return {
        "ref_key": ref_key,
        "number": number,
        "dashboard_date": day.isoformat(),
        "meeting_date": meeting_date,
        "slot": f"{meeting_date} {start_time}–{end_time}",
        "participants": list(participants),
        "scheduled_label": queue_item.get("scheduled_label"),
        "location": queue_item.get("location"),
        "counts": {
            "unapproved": len(unapproved),
            "today": len(today),
            "items": len(items),
        },
    }


async def run(
    *,
    number: str,
    participants: tuple[str, ...],
    meeting_date: str,
    start_time: str,
    end_time: str,
    room: str,
    subject: str,
    ref_key: str | None,
    dashboard_day: date | None,
) -> dict:
    normalized_ref = (ref_key or _ref_key_for_number(number)).strip().lower()
    return await seed_redis(
        ref_key=normalized_ref,
        number=number,
        participants=participants,
        meeting_date=meeting_date,
        start_time=start_time,
        end_time=end_time,
        room=room,
        subject=subject,
        dashboard_day=dashboard_day,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Создать тестовую СЗ в Redis.")
    parser.add_argument("--number", default=DEFAULT_NUMBER)
    parser.add_argument("--meeting-date", default=DEFAULT_MEETING_DATE)
    parser.add_argument("--start", default=DEFAULT_START, help="HH:MM")
    parser.add_argument("--end", default=DEFAULT_END, help="HH:MM")
    parser.add_argument("--room", default=DEFAULT_ROOM)
    parser.add_argument("--subject", default=DEFAULT_SUBJECT)
    parser.add_argument("--ref-key", default=None)
    parser.add_argument(
        "--dashboard-date",
        default=None,
        help="Дата dashboard в Redis (ISO). По умолчанию — сегодня.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    args = build_parser().parse_args(argv)
    dashboard_day = date.fromisoformat(args.dashboard_date) if args.dashboard_date else None
    report = asyncio.run(
        run(
            number=args.number,
            participants=DEFAULT_PARTICIPANTS,
            meeting_date=args.meeting_date,
            start_time=args.start,
            end_time=args.end,
            room=args.room,
            subject=args.subject,
            ref_key=args.ref_key,
            dashboard_day=dashboard_day,
        )
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
