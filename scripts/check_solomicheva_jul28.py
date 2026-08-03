"""Диагностика календаря Соломичевой на 28.07.2026."""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.tools.Outlook.cancel_meeting import to_local
from app.tools.Outlook.outlook_config import build_outlook_config
from app.tools.Outlook.read_calendars import read_calendar_items_in_range
from app.tools.Outlook.slot_search.search import find_nearest_slot


def main() -> None:
    config = build_outlook_config()
    email = "sktb_razvitie2@turbo-don.ru"
    tz = ZoneInfo("Europe/Moscow")
    day_start = datetime(2026, 7, 28, 0, 0, tzinfo=tz)
    day_end = datetime(2026, 7, 29, 0, 0, tzinfo=tz)

    items = read_calendar_items_in_range(
        config,
        email,
        range_start=day_start,
        range_end=day_end,
        max_items=100,
    )
    print("=== 28.07.2026 (MSK) ===")
    for item in sorted(items, key=lambda x: x.start):
        start = to_local(item.start, config)
        end = to_local(item.end, config)
        subject = (getattr(item, "subject", "") or "").strip()
        status = getattr(item, "legacy_free_busy_status", "") or ""
        print(f"{start.strftime('%H:%M')}-{end.strftime('%H:%M')} [{status}] {subject}")

    print()
    for label, preferred in [
        ("28.07 08:00", datetime(2026, 7, 28, 8, 0, tzinfo=tz)),
        ("31.07 08:00", datetime(2026, 7, 31, 8, 0, tzinfo=tz)),
        ("31.07 15:00", datetime(2026, 7, 31, 15, 0, tzinfo=tz)),
    ]:
        result = find_nearest_slot(
            config=config,
            attendees=[email],
            preferred=preferred,
            duration=timedelta(minutes=60),
            max_days=14,
            step=timedelta(minutes=15),
            max_items=500,
            source="calendar",
            workers=1,
            verify_calendar=True,
        )
        slot_start = datetime.fromisoformat(result["slot_start"])
        slot_end = datetime.fromisoformat(result["slot_end"])
        print(
            f"Поиск с {label} -> "
            f"{slot_start.strftime('%d.%m.%Y %H:%M')}-{slot_end.strftime('%H:%M')}"
        )


if __name__ == "__main__":
    main()
