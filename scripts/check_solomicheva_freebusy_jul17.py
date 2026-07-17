"""Проверка Free/Busy Соломичевой на 17.07.2026."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.tools.Outlook.send_meeting_invite import load_config
from app.tools.Outlook.slot_search.availability import is_free_for_attendee
from app.tools.Outlook.slot_search.busy import (
    fetch_busy_intervals_freebusy,
    fetch_busy_intervals_freebusy_events,
    fetch_free_busy_views,
)


def main() -> None:
    config = load_config()
    tz = ZoneInfo(config.timezone)
    email = "sktb_razvitie2@turbo-don.ru"

    day_start = datetime(2026, 7, 17, 8, 0, tzinfo=tz)
    day_end = datetime(2026, 7, 17, 17, 0, tzinfo=tz)
    slot_start = datetime(2026, 7, 17, 15, 20, tzinfo=tz)
    slot_end = datetime(2026, 7, 17, 16, 20, tzinfo=tz)
    duration = slot_end - slot_start

    print(f"Email: {email}")
    print(f"Day: {day_start.date()}")
    print()

    print("=== Free/Busy merged intervals ===")
    busy = fetch_busy_intervals_freebusy(config, [email], day_start, day_end)
    intervals = busy.get(email, [])
    if not intervals:
        print("  (no busy intervals)")
    for start, end in intervals:
        print(f"  {start.strftime('%H:%M')} - {end.strftime('%H:%M')}")

    print()
    print(f"Slot 15:20-16:20 is_free: {is_free_for_attendee(slot_start, duration, intervals, config)}")

    print()
    print("=== Free/Busy from calendar_events ===")
    events_busy = fetch_busy_intervals_freebusy_events(config, [email], day_start, day_end)
    ev_intervals = events_busy.get(email, [])
    if not ev_intervals:
        print("  (no busy intervals)")
    for start, end in ev_intervals:
        print(f"  {start.strftime('%H:%M')} - {end.strftime('%H:%M')}")

    print()
    print(f"Slot 15:20-16:20 is_free (events): {is_free_for_attendee(slot_start, duration, ev_intervals, config)}")

    print()
    print("=== Raw calendar_events ===")
    views = fetch_free_busy_views(config, [email], day_start, day_end)
    view = views.get(email)
    if not view:
        print("  no view returned")
        return

    cal_events = getattr(view, "calendar_events", None) or []
    print(f"count: {len(cal_events)}")
    for ev in cal_events:
        start = getattr(ev, "start", None)
        end = getattr(ev, "end", None)
        subj = getattr(ev, "subject", None) or ""
        busy_type = getattr(ev, "busy_type", None) or getattr(ev, "legacy_free_busy_status", None) or ""
        if start and end:
            s = start if isinstance(start, datetime) else datetime.fromisoformat(str(start))
            e = end if isinstance(end, datetime) else datetime.fromisoformat(str(end))
            if s.tzinfo is None:
                s = s.replace(tzinfo=tz)
            if e.tzinfo is None:
                e = e.replace(tzinfo=tz)
            overlap = s < slot_end and e > slot_start
            mark = " <-- OVERLAPS SLOT" if overlap else ""
            print(f"  {s.strftime('%H:%M')}-{e.strftime('%H:%M')} | {busy_type} | {subj}{mark}")


if __name__ == "__main__":
    main()
