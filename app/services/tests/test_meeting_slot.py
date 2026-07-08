from app.services.meeting_slot import (
    format_planned_start_for_search,
    format_slot_label,
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
