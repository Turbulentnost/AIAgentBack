from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from app.models.meeting_registry import MeetingRegistryEntry
from app.models.enums import MeetingRegistryStage
from app.services.meeting_slot import (
    format_planned_start_for_search,
    format_search_start_after_registry_slot,
    format_slot_label,
    resolve_registry_earlier_slot_window,
    slot_duration_minutes,
)


def test_slot_duration_minutes_parses_iso_with_timezone() -> None:
    minutes = slot_duration_minutes(
        "2026-06-22T08:00:00+03:00",
        "2026-06-22T08:20:00+03:00",
    )

    assert minutes == 20


def test_format_slot_label_for_same_day() -> None:
    label = format_slot_label("2026-06-22T08:00:00+03:00", "2026-06-22T08:20:00+03:00")

    assert label == "22.06.2026, 08:00–08:20"


def test_format_slot_label_converts_utc_to_display_timezone() -> None:
    label = format_slot_label("2026-07-14T11:30:00+00:00", "2026-07-14T12:30:00+00:00")

    assert label == "14.07.2026, 14:30–15:30"


def test_format_event_time_display_for_same_day() -> None:
    from app.services.meeting_slot import format_event_time_display

    start, end = format_event_time_display(
        "2026-07-14T09:00:00+03:00",
        "2026-07-14T09:30:00+03:00",
    )

    assert start == "14.07.2026, 09:00"
    assert end == "09:30"


def test_format_planned_start_combines_date_and_time_from_queue() -> None:
    planned = format_planned_start_for_search(
        None,
        {
            "ЖелаемаяДатаПроведенияСовещания": "2026-06-19T00:00:00",
            "ВремяНачалаСовещания": "0001-01-01T11:00:00",
        },
    )

    assert planned == "2026-06-19 11:00"


def test_format_search_start_after_registry_slot_uses_slot_end() -> None:
    tz = ZoneInfo("Europe/Moscow")
    slot_start = datetime(2026, 7, 10, 15, 0, tzinfo=tz)
    slot_end = datetime(2026, 7, 10, 16, 0, tzinfo=tz)

    assert format_search_start_after_registry_slot(slot_start, slot_end) == "2026-07-10 16:00"


def test_format_search_start_after_registry_slot_falls_back_to_start() -> None:
    slot_start = datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc)

    assert format_search_start_after_registry_slot(slot_start, None) == "2026-07-10 15:00"


def test_format_search_start_after_registry_slot_converts_utc_end() -> None:
    slot_end = datetime(2026, 7, 14, 12, 30, tzinfo=timezone.utc)

    assert format_search_start_after_registry_slot(None, slot_end) == "2026-07-14 15:30"


def test_resolve_registry_earlier_slot_window_uses_desired_date() -> None:
    entry = MeetingRegistryEntry(
        memo_ref_key="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        stage=MeetingRegistryStage.INVITATIONS_SENT,
        invitations_sent_at=datetime(2026, 7, 10, 10, 0, tzinfo=timezone.utc),
        slot_start=datetime(2026, 7, 14, 13, 0, tzinfo=timezone.utc),
        slot_end=datetime(2026, 7, 14, 14, 0, tzinfo=timezone.utc),
    )
    memo_detail = {
        "queue": {"desired_meeting_date": "2026-07-10T00:00:00"},
        "application": {"meeting_start": "2026-07-14T10:00:00+03:00"},
    }

    window = resolve_registry_earlier_slot_window(entry, memo_detail)

    assert window is not None
    assert window.duration_minutes == 60
    assert window.search_from_label == "2026-07-10 08:00"
    assert window.search_until_label == "2026-07-14 16:00"
    assert window.current_slot_label == format_slot_label(
        entry.slot_start.isoformat(),
        entry.slot_end.isoformat(),
    )
