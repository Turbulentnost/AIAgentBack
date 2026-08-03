"""Диагностика detail слота 17.07.2026 15:19–16:19."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from app.tools.Outlook.find_meeting_slot import build_slot_participant_details
from app.tools.Outlook.send_meeting_invite import load_config


def main() -> None:
    config = load_config()
    tz = ZoneInfo("Europe/Moscow")
    slot_start = datetime(2026, 7, 17, 15, 19, tzinfo=tz)
    slot_end = datetime(2026, 7, 17, 16, 19, tzinfo=tz)
    attendees = [
        {"fio": "Соломичева Светлана Викторовна", "email": "sktb_razvitie2@turbo-don.ru", "role": "manager"},
        {"fio": "Мангасарян Давид Каренович", "email": "sktb_razvitie9@turbo-don.ru", "role": "participant"},
        {"fio": "Азарова Анна Александровна", "email": "npo_razvitie9@turbo-don.ru", "role": "participant"},
    ]

    result = build_slot_participant_details(
        config=config,
        attendees=attendees,
        slot_start=slot_start,
        slot_end=slot_end,
        include_company_calendar=True,
        light_reschedule_hints=True,
        verify_personal_calendars=False,
    )

    print(f"company_calendar={config.company_calendar!r}")
    for item in result["participants"]:
        print(f"\n=== {item['fio']} ({item['email']}) -> {item['status']} ===")
        for event in item.get("blocking_events") or []:
            print(
                f"  label={event.get('event_subject')!r} source={event.get('source')} "
                f"time={event.get('event_start')}..{event.get('event_end')} "
                f"hint={event.get('reschedule_hint_label')!r}"
            )


if __name__ == "__main__":
    main()
