from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.tools.Outlook.find_meeting_slot import (
    align_preferred,
    coalesce_intervals,
    find_nearest_slot,
    find_slot_via_busy_gaps,
    freebusy_busy_intervals,
    freebusy_events_busy_intervals,
    freebusy_event_interval,
    is_free_for_all,
    merge_busy_intervals,
    not_before_now,
    union_busy_for_all,
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
    fixed_now = datetime(2026, 6, 19, 8, 0, tzinfo=tz)
    busy_morning = (
        datetime(2026, 6, 19, 10, 0, tzinfo=tz),
        datetime(2026, 6, 19, 11, 0, tzinfo=tz),
    )

    monkeypatch.setattr(
        "app.tools.Outlook.find_meeting_slot.datetime",
        type(
            "FixedDatetime",
            (),
            {
                "now": staticmethod(lambda *_args, **_kwargs: fixed_now),
                "fromisoformat": datetime.fromisoformat,
            },
        ),
    )

    def fake_fetch(*_args, **_kwargs):
        return {attendee: [busy_morning]}

    monkeypatch.setattr(
        "app.tools.Outlook.find_meeting_slot.fetch_all_busy_intervals",
        fake_fetch,
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
        verify_calendar=False,
    )

    slot_start = datetime.fromisoformat(result["slot_start"])
    assert slot_start >= requested
    assert slot_start.hour == 14


def test_find_nearest_slot_never_returns_before_now(monkeypatch) -> None:
    config = _config()
    tz = ZoneInfo("Europe/Moscow")
    attendee = "user@turbo-don.ru"
    fixed_now = datetime(2026, 6, 23, 10, 0, tzinfo=tz)
    requested = datetime(2026, 6, 22, 14, 0, tzinfo=tz)

    monkeypatch.setattr(
        "app.tools.Outlook.find_meeting_slot.datetime",
        type(
            "FixedDatetime",
            (),
            {
                "now": staticmethod(lambda *_args, **_kwargs: fixed_now),
                "fromisoformat": datetime.fromisoformat,
            },
        ),
    )

    def fake_fetch(*_args, **_kwargs):
        return {attendee: []}

    monkeypatch.setattr(
        "app.tools.Outlook.find_meeting_slot.fetch_all_busy_intervals",
        fake_fetch,
    )

    result = find_nearest_slot(
        config=config,
        attendees=[attendee],
        preferred=requested,
        duration=timedelta(minutes=30),
        max_days=7,
        step=timedelta(minutes=15),
        max_items=50,
        source="freebusy",
        workers=1,
        verify_calendar=False,
    )

    slot_start = datetime.fromisoformat(result["slot_start"])
    assert slot_start >= not_before_now(config)
    assert slot_start.date() == fixed_now.date()


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

    assert is_free_for_all(start, duration, busy, _config()) is False


def test_merge_busy_intervals_combines_sources() -> None:
    merged = merge_busy_intervals(
        {"a@x.ru": [(datetime(2026, 1, 1, 10, 0), datetime(2026, 1, 1, 11, 0))]},
        {"a@x.ru": [(datetime(2026, 1, 1, 12, 0), datetime(2026, 1, 1, 13, 0))]},
    )

    assert len(merged["a@x.ru"]) == 2


def test_freebusy_event_interval_ignores_empty_status() -> None:
    config = _config()
    tz = ZoneInfo("Europe/Moscow")
    event = type(
        "Event",
        (),
        {
            "busy_type": "",
            "start": datetime(2026, 6, 20, 10, 0, tzinfo=tz),
            "end": datetime(2026, 6, 20, 11, 0, tzinfo=tz),
        },
    )()

    assert freebusy_event_interval(event, config) is None


def test_freebusy_event_interval_ignores_nodata_status() -> None:
    config = _config()
    tz = ZoneInfo("Europe/Moscow")
    event = type(
        "Event",
        (),
        {
            "busy_type": "NoData",
            "start": datetime(2026, 6, 20, 10, 0, tzinfo=tz),
            "end": datetime(2026, 6, 20, 11, 0, tzinfo=tz),
        },
    )()

    assert freebusy_event_interval(event, config) is None


def test_freebusy_busy_intervals_prefers_merged_when_events_empty() -> None:
    config = _config()
    tz = ZoneInfo("Europe/Moscow")
    start = datetime(2026, 6, 20, 8, 0, tzinfo=tz)
    end = datetime(2026, 6, 20, 9, 0, tzinfo=tz)
    view = type("View", (), {"calendar_events": [], "merged": "22"})()

    intervals = freebusy_busy_intervals(
        view,
        attendee="user@turbo-don.ru",
        range_start=start,
        range_end=end,
        config=config,
    )

    assert intervals == [(start, end)]


def test_busy_intervals_from_merged_string_treats_tentative_as_busy() -> None:
    config = _config()
    tz = ZoneInfo("Europe/Moscow")
    start = datetime(2026, 6, 20, 10, 0, tzinfo=tz)
    end = datetime(2026, 6, 20, 11, 0, tzinfo=tz)
    view = type("View", (), {"calendar_events": [], "merged": "11"})()

    intervals = freebusy_busy_intervals(
        view,
        attendee="user@turbo-don.ru",
        range_start=start,
        range_end=end,
        config=config,
    )

    assert intervals == [(start, end)]


def test_freebusy_busy_intervals_prefers_merged_over_calendar_events() -> None:
    config = _config()
    tz = ZoneInfo("Europe/Moscow")
    start = datetime(2026, 6, 20, 8, 0, tzinfo=tz)
    end = datetime(2026, 6, 20, 10, 0, tzinfo=tz)
    busy_event = type(
        "Event",
        (),
        {
            "busy_type": "Busy",
            "start": datetime(2026, 6, 20, 8, 0, tzinfo=tz),
            "end": datetime(2026, 6, 20, 10, 0, tzinfo=tz),
        },
    )()
    view = type("View", (), {"calendar_events": [busy_event], "merged": "0000"})()

    intervals = freebusy_busy_intervals(
        view,
        attendee="user@turbo-don.ru",
        range_start=start,
        range_end=end,
        config=config,
    )

    assert intervals == []


def test_freebusy_events_busy_intervals_prefers_events_over_merged() -> None:
    config = _config()
    tz = ZoneInfo("Europe/Moscow")
    start = datetime(2026, 6, 20, 8, 0, tzinfo=tz)
    end = datetime(2026, 6, 20, 10, 0, tzinfo=tz)
    busy_event = type(
        "Event",
        (),
        {
            "busy_type": "Busy",
            "start": datetime(2026, 6, 20, 8, 0, tzinfo=tz),
            "end": datetime(2026, 6, 20, 10, 0, tzinfo=tz),
        },
    )()
    view = type("View", (), {"calendar_events": [busy_event], "merged": "0000"})()

    intervals = freebusy_events_busy_intervals(
        view,
        attendee="user@turbo-don.ru",
        range_start=start,
        range_end=end,
        config=config,
    )

    assert intervals == [(start, end)]


def test_union_busy_finds_gap_between_participants() -> None:
    config = _config()
    tz = ZoneInfo("Europe/Moscow")
    day = datetime(2026, 6, 23, 0, 0, tzinfo=tz)
    busy = {
        "a@turbo-don.ru": [(datetime(2026, 6, 23, 10, 0, tzinfo=tz), datetime(2026, 6, 23, 11, 0, tzinfo=tz))],
        "b@turbo-don.ru": [(datetime(2026, 6, 23, 14, 0, tzinfo=tz), datetime(2026, 6, 23, 15, 0, tzinfo=tz))],
    }
    earliest = datetime(2026, 6, 23, 8, 0, tzinfo=tz)
    search_end = datetime(2026, 6, 23, 17, 0, tzinfo=tz)
    union = union_busy_for_all(busy, config, earliest, search_end)
    slot, checked = find_slot_via_busy_gaps(
        earliest_allowed=earliest,
        search_end=search_end,
        duration=timedelta(minutes=30),
        step=timedelta(minutes=15),
        union_busy=union,
        config=config,
    )
    assert slot is not None
    assert slot.hour == 8
    assert checked < 20


def test_coalesce_intervals_merges_overlap() -> None:
    config = _config()
    tz = ZoneInfo("Europe/Moscow")
    merged = coalesce_intervals(
        [
            (datetime(2026, 6, 23, 10, 0, tzinfo=tz), datetime(2026, 6, 23, 11, 0, tzinfo=tz)),
            (datetime(2026, 6, 23, 10, 30, tzinfo=tz), datetime(2026, 6, 23, 12, 0, tzinfo=tz)),
        ],
        config,
    )
    assert len(merged) == 1
    assert merged[0][1].hour == 12


def test_find_nearest_slot_retries_when_calendar_rejects_freebusy_slot(monkeypatch) -> None:
    config = _config()
    tz = ZoneInfo("Europe/Moscow")
    attendee = "user@turbo-don.ru"
    requested = datetime(2026, 6, 23, 8, 0, tzinfo=tz)
    fixed_now = datetime(2026, 6, 23, 8, 0, tzinfo=tz)
    accepted_slot = datetime(2026, 6, 23, 11, 0, tzinfo=tz)

    monkeypatch.setattr(
        "app.tools.Outlook.find_meeting_slot.datetime",
        type(
            "FixedDatetime",
            (),
            {
                "now": staticmethod(lambda *_args, **_kwargs: fixed_now),
                "fromisoformat": datetime.fromisoformat,
            },
        ),
    )

    monkeypatch.setattr(
        "app.tools.Outlook.find_meeting_slot.fetch_all_busy_intervals",
        lambda *_args, **_kwargs: {attendee: []},
    )

    def fake_verify(*, slot_start, **_kwargs):
        if slot_start < accepted_slot:
            return False, {attendee: [(slot_start, slot_start + timedelta(minutes=30))]}
        return True, {attendee: []}

    monkeypatch.setattr(
        "app.tools.Outlook.find_meeting_slot.verify_slot_with_calendar",
        fake_verify,
    )

    result = find_nearest_slot(
        config=config,
        attendees=[attendee],
        preferred=requested,
        duration=timedelta(minutes=30),
        max_days=1,
        step=timedelta(minutes=15),
        max_items=50,
        source="freebusy",
        workers=1,
        verify_calendar=True,
    )

    slot_start = datetime.fromisoformat(result["slot_start"])
    assert slot_start >= accepted_slot
