from app.services.meeting_slot import format_slot_label, slot_duration_minutes


def test_slot_duration_minutes_parses_iso_with_timezone() -> None:
    minutes = slot_duration_minutes(
        "2026-06-22T08:00:00+03:00",
        "2026-06-22T08:20:00+03:00",
    )

    assert minutes == 20


def test_format_slot_label_for_same_day() -> None:
    label = format_slot_label("2026-06-22T08:00:00+03:00", "2026-06-22T08:20:00+03:00")

    assert label == "22.06.2026, 08:00–08:20"
