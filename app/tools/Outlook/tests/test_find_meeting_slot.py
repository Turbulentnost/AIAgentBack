from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.tools.Outlook.find_meeting_slot import (
    align_preferred,
    busy_intervals_from_freebusy_view,
    busy_intervals_from_merged_string,
    find_nearest_slot,
    is_free_for_all,
    merge_busy_intervals,
)
from app.tools.Outlook.outlook_config import OutlookConfig


def _config() -> OutlookConfig:
    return OutlookConfig(
        email="svc@turbo-don.ru",
        password="secret",
        server="mail.turbo-don.ru",
        mailbox="postagent@turbo-don.ru",
        timezone="Europe/Moscow",
        smtp_host="mail.turbo-don.ru",
        smtp_port=587,
        smtp_use_tls=True,
        smtp_from="postagent@turbo-don.ru",
    )


def test_align_preferred_keeps_requested_afternoon_time() -> None:
    config = _config()
    preferred = datetime(2026, 6, 19, 14, 0, tzinfo=ZoneInfo("Europe/Moscow"))

    aligned = align_preferred(preferred, config)

    assert aligned.hour == 14
    assert aligned.minute == 0


def test_find_nearest_slot_never_returns_before_requested_time(monkeypatch) -> None:
    config = _config()
    tz = ZoneInfo("Europe/Moscow")
    attendee = "user@turbo-don.ru"
    requested = datetime(2026, 6, 19, 14, 0, tzinfo=tz)
    busy_morning = (
        datetime(2026, 6, 19, 10, 0, tzinfo=tz),
        datetime(2026, 6, 19, 11, 0, tzinfo=tz),
    )

    def fake_fetch(*_args, **_kwargs):
        return {attendee: [busy_morning]}

    def fake_verify(*, slot_start, **_kwargs):
        return True, {attendee: []}

    monkeypatch.setattr(
        "app.tools.Outlook.find_meeting_slot.fetch_all_busy_intervals",
        fake_fetch,
    )
    monkeypatch.setattr(
        "app.tools.Outlook.find_meeting_slot.verify_slot_with_calendar",
        fake_verify,
    )

    result = find_nearest_slot(
        config=config,
        attendees=[attendee],
        preferred=requested,
        duration=timedelta(minutes=20),
        max_days=1,
        step=timedelta(minutes=15),
        max_items=50,
        source="freebusy",
        workers=1,
    )

    slot_start = datetime.fromisoformat(result["slot_start"])
    assert slot_start >= requested
    assert slot_start.hour == 14


def test_is_free_for_all_detects_overlap() -> None:
    tz = ZoneInfo("Europe/Moscow")
    start = datetime(2026, 6, 20, 14, 0, tzinfo=tz)
    duration = timedelta(minutes=30)
    busy = {
        "a@turbo-don.ru": [
            (
                datetime(2026, 6, 20, 14, 15, tzinfo=tz),
                datetime(2026, 6, 20, 15, 0, tzinfo=tz),
            )
        ]
    }

    assert is_free_for_all(start, duration, busy) is False


def test_merge_busy_intervals_combines_sources() -> None:
    merged = merge_busy_intervals(
        {"a@x.ru": [(datetime(2026, 1, 1, 10, 0), datetime(2026, 1, 1, 11, 0))]},
        {"a@x.ru": [(datetime(2026, 1, 1, 12, 0), datetime(2026, 1, 1, 13, 0))]},
    )

    assert len(merged["a@x.ru"]) == 2


def test_busy_intervals_from_merged_string_all_free() -> None:
    config = _config()
    tz = ZoneInfo("Europe/Moscow")
    range_start = datetime(2026, 6, 19, 8, 0, tzinfo=tz)
    range_end = datetime(2026, 6, 19, 17, 0, tzinfo=tz)

    intervals = busy_intervals_from_merged_string(
        "0" * 18,
        range_start,
        range_end,
        config,
    )

    assert intervals == []


def test_busy_intervals_from_merged_string_detects_busy_block() -> None:
    config = _config()
    tz = ZoneInfo("Europe/Moscow")
    range_start = datetime(2026, 6, 19, 8, 0, tzinfo=tz)
    range_end = datetime(2026, 6, 19, 17, 0, tzinfo=tz)

    intervals = busy_intervals_from_merged_string(
        "002200",
        range_start,
        range_end,
        config,
    )

    assert intervals == [
        (
            datetime(2026, 6, 19, 9, 0, tzinfo=tz),
            datetime(2026, 6, 19, 10, 0, tzinfo=tz),
        )
    ]


def test_busy_intervals_from_freebusy_view_uses_merged_when_no_events() -> None:
    config = _config()
    tz = ZoneInfo("Europe/Moscow")
    range_start = datetime(2026, 6, 19, 8, 0, tzinfo=tz)
    range_end = datetime(2026, 6, 19, 17, 0, tzinfo=tz)

    class FakeView:
        calendar_events = None
        merged = "0" * 24

    intervals = busy_intervals_from_freebusy_view(
        FakeView(),
        "postagant@turbo-don.ru",
        range_start,
        range_end,
        config,
    )

    assert intervals == []
