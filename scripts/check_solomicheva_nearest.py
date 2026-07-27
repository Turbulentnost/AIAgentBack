"""Диагностика ближайшего слота Соломичевой (Free/Busy merged vs events)."""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from app.tools.Outlook.outlook_config import build_outlook_config
from app.tools.Outlook.slot_search.busy import (
    coalesce_intervals,
    fetch_busy_intervals_freebusy,
    fetch_busy_intervals_freebusy_events,
)
from app.tools.Outlook.slot_search.iteration import find_slot_via_busy_gaps, first_valid_slot_in_window
from app.tools.Outlook.slot_search.rules import not_before_now
from app.tools.Outlook.slot_search.search import (
    find_nearest_slots_per_attendee,
    search_bounds_for_preferred,
)


def _print_day_intervals(label: str, intervals: list[tuple[datetime, datetime]], day: datetime) -> None:
    print(f"=== {label} on {day.date()} ===")
    found = False
    for start, end in intervals:
        if start.date() == day.date():
            found = True
            print(f"  {start.strftime('%H:%M')}-{end.strftime('%H:%M')}")
    if not found:
        print("  (no intervals)")


def main() -> None:
    config = build_outlook_config()
    email = "sktb_razvitie2@turbo-don.ru"
    tz = ZoneInfo("Europe/Moscow")
    fixed_now = datetime(2026, 7, 17, 11, 15, tzinfo=tz)
    day28 = datetime(2026, 7, 28, tzinfo=tz)

    with patch("app.tools.Outlook.slot_search.rules.datetime") as dt:
        dt.now = lambda _tz=None: fixed_now
        dt.fromisoformat = datetime.fromisoformat
        preferred = not_before_now(config)
        _, earliest, search_end = search_bounds_for_preferred(config, preferred, 30)
        fb = fetch_busy_intervals_freebusy(config, [email], earliest, search_end)
        fbe = fetch_busy_intervals_freebusy_events(config, [email], earliest, search_end)

    _print_day_intervals("Free/Busy merged", fb.get(email, []), day28)
    _print_day_intervals("Free/Busy events", fbe.get(email, []), day28)

    with patch("app.tools.Outlook.slot_search.rules.datetime") as dt:
        dt.now = lambda _tz=None: fixed_now
        dt.fromisoformat = datetime.fromisoformat
        merged_result = find_nearest_slots_per_attendee(
            config=config,
            attendees=[email],
            preferred=not_before_now(config),
            duration=timedelta(minutes=60),
            max_days=30,
            step=timedelta(minutes=15),
        )

    print("=== find_nearest_slots_per_attendee (events) ===")
    print(merged_result.get(email))

    with patch("app.tools.Outlook.slot_search.rules.datetime") as dt:
        dt.now = lambda _tz=None: fixed_now
        dt.fromisoformat = datetime.fromisoformat
        preferred = not_before_now(config)
        _, earliest, search_end = search_bounds_for_preferred(config, preferred, 30)
        busy = coalesce_intervals(
            fbe.get(email, []),
            config,
            clip_start=earliest,
            clip_end=search_end,
        )
        slot, checked = find_slot_via_busy_gaps(
            earliest_allowed=earliest,
            search_end=search_end,
            duration=timedelta(minutes=60),
            step=timedelta(minutes=15),
            union_busy=busy,
            config=config,
        )

    print("=== gap scan with events busy ===")
    print(f"checked={checked}")
    if slot:
        end = slot + timedelta(minutes=60)
        print(f"slot: {slot.isoformat()} -> {end.isoformat()}")

    with patch("app.tools.Outlook.slot_search.rules.datetime") as dt:
        dt.now = lambda _tz=None: fixed_now
        dt.fromisoformat = datetime.fromisoformat
        preferred = not_before_now(config)
        _, earliest, search_end = search_bounds_for_preferred(config, preferred, 30)
        fb2 = fetch_busy_intervals_freebusy(config, [email], earliest, search_end)
        busy2 = coalesce_intervals(
            fb2.get(email, []),
            config,
            clip_start=earliest,
            clip_end=search_end,
        )

    print("=== gap trace (first hit) ===")
    window_start = earliest
    for index, (busy_start, busy_end) in enumerate(busy2):
        if busy_start > search_end:
            break
        if busy_start > window_start:
            slot, _wc = first_valid_slot_in_window(
                window_start,
                min(busy_start, search_end),
                duration=timedelta(minutes=60),
                step=timedelta(minutes=15),
                config=config,
            )
            if slot is not None:
                print(
                    f"gap #{index}: {window_start.strftime('%m-%d %H:%M')} .. "
                    f"{busy_start.strftime('%m-%d %H:%M')} -> {slot.isoformat()}"
                )
                break
        window_start = max(window_start, busy_end)


if __name__ == "__main__":
    main()
