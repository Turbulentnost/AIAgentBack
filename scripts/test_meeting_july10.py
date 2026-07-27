"""Тестовый прогон: СЗ на совещание 10.07 13:00-14:00 + приглашение Outlook."""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

from app.agents.meeting_agent.memo_presenter import build_queue_item_from_row
from app.core.config import settings
from app.services.meeting_dashboard_cache import MeetingDashboardCacheService
from app.services.meeting_invite_format import (
    INVITE_AGENT_FOOTER,
    format_invite_body,
    format_invite_location,
    resolve_room_for_location,
)
from app.services.meeting_memo_cache import MeetingMemoCacheService, build_detail_from_dashboard_item
from app.agents.meeting_agent.dashboard import merge_dashboard_items
from app.tools.Outlook.send_meeting_invite import dispatch_meeting_invite
from app.tools.onec.connection import CONFIG, create_session
from app.tools.onec.get_meetings import DOCUMENT_ENTITY, entity_url, meeting_theme, resolve_theme_key, load_metadata_xml
from app.tools.onec.lookup_email_by_fio import lookup_email_by_fio

TEST_REF_KEY = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
MEETING_DATE = "2026-07-10"
START = "2026-07-10 13:00"
END = "2026-07-10 14:00"
ROOM = "Зал совещаний КБ"
PARTICIPANTS = (
    "Соломичева Светлана Викторовна",
    "Комарькова Анастасия Эдуардовна",
)
SUBJECT = "Тест агента: согласование плана работ (10.07.2026)"


def try_create_memo_in_onec() -> dict:
    session = create_session(CONFIG)
    metadata = load_metadata_xml(session, CONFIG)
    theme_key = resolve_theme_key(session, CONFIG, metadata)
    memo_ref = str(uuid.uuid4())
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    payload: dict[str, object] = {
        "Ref_Key": memo_ref,
        "Date": now,
        "DeletionMark": False,
        "Posted": False,
        "ТемаСлужебнойЗаписки": meeting_theme(),
        "ТемаСовещания": SUBJECT,
        "ЦельПланаСовещания": "Проверка сценария планирования совещания агентом.",
        "ЖелаемаяДатаПроведенияСовещания": f"{MEETING_DATE}T00:00:00",
        "ВремяНачалаСовещания": f"{MEETING_DATE}T13:00:00",
        "ВремяОкончанияСовещания": f"{MEETING_DATE}T14:00:00",
        "МестоПроведенияСовещания": ROOM,
        "Статус": "НеСогласована",
        "ВидСовещания": "Внеплановое",
        "ПланСовещания": [
            {"LineNumber": 1, "Задача": "Проверить интеграцию с Outlook"},
            {"LineNumber": 2, "Задача": "Согласовать дальнейшие шаги"},
        ],
        "СписокУчастников": [
            {"LineNumber": 1, "Участник": PARTICIPANTS[0]},
            {"LineNumber": 2, "Участник": PARTICIPANTS[1]},
        ],
    }
    if theme_key:
        payload["ТемаСлужебнойЗаписки_Key"] = theme_key
        payload["ТемаСлужебнойЗаписки_Type"] = "StandardODATA.Catalog_ТД_ТемыСлужебныхЗаписок"

    response = session.post(
        f"{entity_url(CONFIG.url, DOCUMENT_ENTITY)}?$format=json",
        json=payload,
        timeout=CONFIG.timeout,
    )
    if not response.ok:
        return {
            "created": False,
            "status_code": response.status_code,
            "error": response.text[:800],
        }
    body = response.json()
    return {
        "created": True,
        "ref_key": body.get("Ref_Key", memo_ref),
        "number": body.get("Number"),
        "date": body.get("Date"),
    }


def build_queue_item(*, ref_key: str, number: str) -> dict:
    header = {
        "Ref_Key": ref_key,
        "Number": number,
        "Date": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
        "Статус": "НеСогласована",
        "ТемаСлужебнойЗаписки": meeting_theme(),
        "ТемаСовещания": SUBJECT,
        "ЖелаемаяДатаПроведенияСовещания": f"{MEETING_DATE}T00:00:00",
        "ВремяНачалаСовещания": f"{MEETING_DATE}T13:00:00",
        "ВремяОкончанияСовещания": f"{MEETING_DATE}T14:00:00",
        "МестоПроведенияСовещания": ROOM,
        "ВидСовещания": "Внеплановое",
        "СписокУчастников": [
            {"LineNumber": 1, "Участник": PARTICIPANTS[0]},
            {"LineNumber": 2, "Участник": PARTICIPANTS[1]},
        ],
    }
    item = build_queue_item_from_row(header)
    item.update(
        {
            "title": SUBJECT,
            "subject": SUBJECT,
            "location": ROOM,
            "participant_names": list(PARTICIPANTS),
            "participants_count": len(PARTICIPANTS),
            "manager": {"full_name": PARTICIPANTS[1]},
            "initiator": {"full_name": PARTICIPANTS[1]},
            "meeting_start": f"{MEETING_DATE}T13:00:00",
            "meeting_end": f"{MEETING_DATE}T14:00:00",
        }
    )
    return item


async def seed_redis_test_memo(*, ref_key: str, number: str) -> dict:
    queue_item = build_queue_item(ref_key=ref_key, number=number)
    detail = build_detail_from_dashboard_item(queue_item)
    detail["application"]["participants"] = [
        {
            "full_name": PARTICIPANTS[0],
            "email": lookup_email_by_fio(PARTICIPANTS[0])["emails"][0]["email"],
            "ref_key": None,
            "department": None,
        },
        {
            "full_name": PARTICIPANTS[1],
            "email": lookup_email_by_fio(PARTICIPANTS[1])["emails"][0]["email"],
            "ref_key": None,
            "department": None,
        },
    ]
    detail["application"]["manager"] = {
        "full_name": PARTICIPANTS[1],
        "email": lookup_email_by_fio(PARTICIPANTS[1])["emails"][0]["email"],
    }
    detail["application"]["initiator"] = detail["application"]["manager"]

    memo_service = MeetingMemoCacheService()
    dashboard_service = MeetingDashboardCacheService()
    # Очередь в UI читается по сегодняшней дате, не по дате совещания.
    dashboard_day = date.today()
    fetched_at = datetime.now(timezone.utc)
    await memo_service._write_cache(ref_key, detail, fetched_at=fetched_at)

    existing = await dashboard_service._read_cache(dashboard_day)
    if existing is not None:
        unapproved = [
            item
            for item in (existing.get("unapproved") or [])
            if (item.get("ref_key") or "").lower() != ref_key.lower()
        ]
        today = [
            item
            for item in (existing.get("today") or [])
            if (item.get("ref_key") or "").lower() != ref_key.lower()
        ]
    else:
        unapproved = []
        today = []

    unapproved.append(queue_item)
    today.append(queue_item)
    items = merge_dashboard_items(unapproved, today)
    await dashboard_service._write_cache(
        dashboard_day,
        {
            "date": dashboard_day.isoformat(),
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
        "detail": detail,
        "queue_item": queue_item,
        "dashboard_date": dashboard_day.isoformat(),
    }


def resolve_participant_emails() -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for fio in PARTICIPANTS:
        pairs.append((fio, lookup_email_by_fio(fio)["emails"][0]["email"]))
    return pairs


def send_test_invite(*, dry_run: bool) -> dict:
    attendee_pairs = resolve_participant_emails()
    room = resolve_room_for_location(ROOM)
    if not room:
        raise RuntimeError(f"Не найдена переговорная в meeting_rooms.json: {ROOM}")

    location = format_invite_location(PARTICIPANTS[1], ROOM)
    body = format_invite_body(attendee_pairs, room=room, footer=INVITE_AGENT_FOOTER)
    emails = [email for _, email in attendee_pairs]
    resources = [room["email"]]
    request = {
        "attendee": emails[0],
        "attendees": emails,
        "subject": SUBJECT,
        "start": START,
        "duration_minutes": 60,
        "body": body,
        "location": location,
        "resources": resources,
    }
    if dry_run:
        return {"dry_run": True, "request": request}
    result = dispatch_meeting_invite(**request)
    return {"invite": result, "request": request}


async def run(*, dry_run: bool, skip_invite: bool) -> dict:
    report: dict[str, object] = {
        "meeting_date": MEETING_DATE,
        "start": START,
        "end": END,
        "room": ROOM,
        "participants": list(PARTICIPANTS),
    }

    onec_result = try_create_memo_in_onec()
    report["onec_create"] = onec_result

    ref_key = str(onec_result.get("ref_key") or TEST_REF_KEY) if onec_result.get("created") else TEST_REF_KEY
    number = str(onec_result.get("number") or "TEST-000001")
    cache_result = await seed_redis_test_memo(ref_key=ref_key, number=number)
    report["redis_cache"] = {
        "ref_key": cache_result["ref_key"],
        "number": cache_result["number"],
        "dashboard_date": cache_result["dashboard_date"],
        "scheduled_label": cache_result["queue_item"].get("scheduled_label"),
        "location": cache_result["queue_item"].get("location"),
    }

    if not skip_invite:
        report["outlook_invite"] = send_test_invite(dry_run=dry_run)

    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Тест: СЗ на совещание 10.07 13:00-14:00.")
    parser.add_argument("--dry-run", action="store_true", help="Не отправлять приглашение в Outlook")
    parser.add_argument("--skip-invite", action="store_true", help="Только 1С/кэш, без Outlook")
    parser.add_argument("-o", "--output", help="Сохранить JSON-отчёт")
    return parser


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    import asyncio

    args = build_parser().parse_args(argv)
    report = asyncio.run(run(dry_run=args.dry_run, skip_invite=args.skip_invite))
    text = json.dumps(report, ensure_ascii=False, indent=2, default=str)

    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
        print(f"Сохранено: {args.output}")
    else:
        print(text)

    onec = report.get("onec_create") or {}
    if onec.get("created"):
        print("\nOK: СЗ создана в 1С")
        print(f"  ref_key: {onec.get('ref_key')}")
        print(f"  number: {onec.get('number')}")
    else:
        print("\nWARN: 1С недоступна для создания — СЗ загружена в Redis-кэш для теста")
        print(f"  ref_key: {report['redis_cache']['ref_key']}")

    if not args.skip_invite and not args.dry_run:
        invite = (report.get("outlook_invite") or {}).get("invite") or {}
        if invite.get("status") == "sent":
            print("OK: приглашение отправлено в Outlook")
            print(f"  Кому: {', '.join(invite.get('attendees') or [])}")
            print(f"  Переговорная: {', '.join(invite.get('resources') or [])}")
            if invite.get("outlook_meeting_url"):
                print(f"  Ссылка: {invite['outlook_meeting_url']}")
    elif not args.skip_invite:
        print("OK: приглашение подготовлено (dry-run)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
