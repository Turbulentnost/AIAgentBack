from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from app.tools.Outlook.find_meeting_slot import (
    align_preferred,
    attach_reschedule_hints,
    coalesce_intervals,
    coverage_ratios,
    find_nearest_slot,
    find_quorum_slots,
    find_slot_via_busy_gaps,
    build_slot_participant_details,
    conflicting_calendar_items_at_slot,
    dedupe_conflict_records,
    movability_reason,
    preliminary_slot_impact,
    quorum_search_start,
    slot_impact_score,
    suggest_reschedule_window,
    calendar_item_attendee_emails,
    freebusy_busy_intervals,
    freebusy_events_busy_intervals,
    freebusy_event_interval,
    is_free_for_all,
    intervals_overlap,
    merge_busy_intervals,
    movability_score,
    not_before_now,
    partition_attendees_at_slot,
    union_busy_for_all,
    find_company_calendar_reschedule_candidates,
)
from app.tools.Outlook.outlook_config import OutlookConfig


def _config() -> OutlookConfig:
    return OutlookConfig(
        email="svc@turbo-don.ru",
        password="secret",
        server="mail.turbo-don.ru",
        web_app_url="",
        mailbox="postagent@turbo-don.ru",
        timezone="Europe/Moscow",
        smtp_host="mail.turbo-don.ru",
        smtp_port=587,
        smtp_use_tls=True,
        smtp_from="postagent@turbo-don.ru",
        company_calendar="calendar@turbo-don.ru",
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
        "app.tools.Outlook.slot_search.rules.datetime",
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
        "app.tools.Outlook.slot_search.search.fetch_all_busy_intervals",
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
        "app.tools.Outlook.slot_search.rules.datetime",
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
        "app.tools.Outlook.slot_search.search.fetch_all_busy_intervals",
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
        "app.tools.Outlook.slot_search.rules.datetime",
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
        "app.tools.Outlook.slot_search.search.fetch_all_busy_intervals",
        lambda *_args, **_kwargs: {attendee: []},
    )

    def fake_verify(*, slot_start, **_kwargs):
        if slot_start < accepted_slot:
            return False, {attendee: [(slot_start, slot_start + timedelta(minutes=30))]}
        return True, {attendee: []}

    monkeypatch.setattr(
        "app.tools.Outlook.slot_search.search.verify_slot_with_calendar",
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


def test_find_nearest_slot_calendar_source_finds_midday_gap(monkeypatch) -> None:
    config = _config()
    tz = ZoneInfo("Europe/Moscow")
    attendee = "manager@turbo-don.ru"
    requested = datetime(2026, 7, 28, 8, 0, tzinfo=tz)
    fixed_now = datetime(2026, 7, 17, 8, 0, tzinfo=tz)
    busy_blocks = [
        (datetime(2026, 7, 28, 8, 0, tzinfo=tz), datetime(2026, 7, 28, 11, 0, tzinfo=tz)),
        (datetime(2026, 7, 28, 13, 0, tzinfo=tz), datetime(2026, 7, 28, 17, 0, tzinfo=tz)),
    ]

    monkeypatch.setattr(
        "app.tools.Outlook.slot_search.rules.datetime",
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
        "app.tools.Outlook.slot_search.search.fetch_all_busy_intervals",
        lambda *_args, **_kwargs: {attendee: busy_blocks},
    )

    verify_called = False

    def _unexpected_verify(**_kwargs):
        nonlocal verify_called
        verify_called = True
        return False, {attendee: busy_blocks}

    monkeypatch.setattr(
        "app.tools.Outlook.slot_search.search.verify_slot_with_calendar",
        _unexpected_verify,
    )

    result = find_nearest_slot(
        config=config,
        attendees=[attendee],
        preferred=requested,
        duration=timedelta(minutes=60),
        max_days=14,
        step=timedelta(minutes=15),
        max_items=50,
        source="calendar",
        workers=1,
        verify_calendar=True,
    )

    slot_start = datetime.fromisoformat(result["slot_start"])
    assert slot_start.date().isoformat() == "2026-07-28"
    assert slot_start.hour == 11
    assert verify_called is False


def test_movability_score_marks_committee_as_low() -> None:
    assert movability_score(busy_type="Busy", subject="Заседание комитета") == "low"
    assert movability_score(busy_type="Tentative", subject="Sync") == "high"


def test_find_quorum_slots_prefers_majority_over_full_overlap(monkeypatch) -> None:
    config = _config()
    tz = ZoneInfo("Europe/Moscow")
    attendees = [
        "a@turbo-don.ru",
        "b@turbo-don.ru",
        "c@turbo-don.ru",
        "d@turbo-don.ru",
    ]
    requested = datetime(2026, 6, 19, 10, 0, tzinfo=tz)
    fixed_now = datetime(2026, 6, 19, 8, 0, tzinfo=tz)
    c_block = (
        datetime(2026, 6, 19, 8, 0, tzinfo=tz),
        datetime(2026, 6, 19, 17, 0, tzinfo=tz),
    )

    monkeypatch.setattr(
        "app.tools.Outlook.slot_search.rules.datetime",
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
        return {
            "a@turbo-don.ru": [],
            "b@turbo-don.ru": [],
            "c@turbo-don.ru": [c_block],
            "d@turbo-don.ru": [],
        }

    monkeypatch.setattr(
        "app.tools.Outlook.slot_search.search.fetch_all_busy_intervals",
        fake_fetch,
    )
    monkeypatch.setattr(
        "app.tools.Outlook.slot_search.search.verify_slot_with_calendar",
        lambda **_kwargs: (True, fake_fetch()),
    )
    monkeypatch.setattr(
        "app.tools.Outlook.slot_search.scoring.fetch_freebusy_calendar_events",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        "app.tools.Outlook.slot_search.conflicts.read_calendar_items_in_range",
        lambda *_args, **_kwargs: [],
    )

    result = find_quorum_slots(
        config=config,
        attendees=attendees,
        required_attendees=["a@turbo-don.ru", "b@turbo-don.ru"],
        preferred=requested,
        duration=timedelta(minutes=60),
        max_days=1,
        step=timedelta(minutes=60),
        max_items=50,
        source="freebusy",
        workers=1,
        min_coverage_ratio=0.7,
        max_results=1,
        verify_top_n=0,
        verify_calendar=False,
    )

    candidate = result["candidates"][0]
    assert candidate["coverage"]["free"] == 3
    assert candidate["coverage"]["total"] == 4
    assert "c@turbo-don.ru" in candidate["busy_attendees"]
    assert len(candidate["conflicts"]) >= 1


def test_quorum_search_start_uses_workday_beginning_not_preferred_time(monkeypatch) -> None:
    config = _config()
    tz = ZoneInfo("Europe/Moscow")
    fixed_now = datetime(2026, 7, 7, 9, 0, tzinfo=tz)
    preferred = datetime(2026, 7, 14, 10, 0, tzinfo=tz)

    monkeypatch.setattr(
        "app.tools.Outlook.slot_search.rules.datetime",
        type(
            "FixedDatetime",
            (),
            {
                "now": staticmethod(lambda *_args, **_kwargs: fixed_now),
                "fromisoformat": datetime.fromisoformat,
            },
        ),
    )

    start = quorum_search_start(preferred, config)
    assert start == datetime(2026, 7, 14, 8, 0, tzinfo=tz)


def test_find_quorum_slots_scans_before_preferred_time(monkeypatch) -> None:
    config = _config()
    tz = ZoneInfo("Europe/Moscow")
    attendees = [
        "a@turbo-don.ru",
        "b@turbo-don.ru",
        "c@turbo-don.ru",
        "d@turbo-don.ru",
    ]
    requested = datetime(2026, 7, 14, 10, 0, tzinfo=tz)
    fixed_now = datetime(2026, 7, 7, 9, 0, tzinfo=tz)
    manager_block = (
        datetime(2026, 7, 14, 10, 0, tzinfo=tz),
        datetime(2026, 7, 14, 11, 0, tzinfo=tz),
    )
    manager_afternoon = (
        datetime(2026, 7, 14, 13, 0, tzinfo=tz),
        datetime(2026, 7, 14, 16, 0, tzinfo=tz),
    )

    monkeypatch.setattr(
        "app.tools.Outlook.slot_search.rules.datetime",
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
        return {
            "a@turbo-don.ru": [],
            "b@turbo-don.ru": [manager_block, manager_afternoon],
            "c@turbo-don.ru": [],
            "d@turbo-don.ru": [],
        }

    monkeypatch.setattr(
        "app.tools.Outlook.slot_search.search.fetch_all_busy_intervals",
        fake_fetch,
    )
    monkeypatch.setattr(
        "app.tools.Outlook.slot_search.search.verify_slot_with_calendar",
        lambda **_kwargs: (False, fake_fetch()),
    )

    def fake_events(_config, busy_attendees, *_args, **_kwargs):
        if "b@turbo-don.ru" not in busy_attendees:
            return {}
        event = SimpleNamespace(
            start=manager_block[0],
            end=manager_block[1],
            subject="РГ с руководителем",
            busy_type="Tentative",
        )
        return {"b@turbo-don.ru": [event]}

    monkeypatch.setattr(
        "app.tools.Outlook.slot_search.scoring.fetch_freebusy_calendar_events",
        fake_events,
    )

    result = find_quorum_slots(
        config=config,
        attendees=attendees,
        required_attendees=["a@turbo-don.ru", "b@turbo-don.ru"],
        preferred=requested,
        duration=timedelta(hours=3),
        max_days=1,
        step=timedelta(minutes=60),
        max_items=50,
        source="freebusy",
        workers=1,
        min_coverage_ratio=0.7,
        max_results=1,
        verify_top_n=1,
        verify_calendar=True,
    )

    candidate = result["candidates"][0]
    assert "2026-07-14T09:00:00" in candidate["slot_start"]
    assert candidate["coverage"]["free"] == 3
    assert "b@turbo-don.ru" in candidate["busy_attendees"]
    assert candidate["easy_reschedule_count"] >= 1
    assert candidate["conflicts"][0]["movability"] == "high"


def test_find_quorum_slots_latest_allowed_excludes_later_slots(monkeypatch) -> None:
    config = _config()
    tz = ZoneInfo("Europe/Moscow")
    attendees = ["a@turbo-don.ru", "b@turbo-don.ru"]
    preferred = datetime(2026, 7, 14, 16, 0, tzinfo=tz)
    latest_allowed = datetime(2026, 7, 14, 12, 0, tzinfo=tz)
    fixed_now = datetime(2026, 7, 7, 9, 0, tzinfo=tz)

    monkeypatch.setattr(
        "app.tools.Outlook.slot_search.rules.datetime",
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
        return {"a@turbo-don.ru": [], "b@turbo-don.ru": []}

    monkeypatch.setattr(
        "app.tools.Outlook.slot_search.search.fetch_all_busy_intervals",
        fake_fetch,
    )
    monkeypatch.setattr(
        "app.tools.Outlook.slot_search.search.verify_slot_with_calendar",
        lambda **_kwargs: (True, fake_fetch()),
    )

    result = find_quorum_slots(
        config=config,
        attendees=attendees,
        preferred=preferred,
        duration=timedelta(hours=1),
        max_days=1,
        step=timedelta(minutes=60),
        max_items=50,
        source="freebusy",
        workers=1,
        max_results=5,
        verify_top_n=0,
        verify_calendar=False,
        latest_allowed=latest_allowed,
        raise_if_empty=False,
    )

    assert result["candidates"]
    for candidate in result["candidates"]:
        start = datetime.fromisoformat(candidate["slot_start"])
        assert start < latest_allowed


def test_find_quorum_slots_raise_if_empty_false_returns_empty_list(monkeypatch) -> None:
    config = _config()
    tz = ZoneInfo("Europe/Moscow")
    attendees = ["a@turbo-don.ru"]
    preferred = datetime(2026, 7, 14, 16, 0, tzinfo=tz)
    latest_allowed = datetime(2026, 7, 14, 8, 0, tzinfo=tz)
    fixed_now = datetime(2026, 7, 14, 9, 0, tzinfo=tz)

    monkeypatch.setattr(
        "app.tools.Outlook.slot_search.rules.datetime",
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
        "app.tools.Outlook.slot_search.search.fetch_all_busy_intervals",
        lambda *_args, **_kwargs: {"a@turbo-don.ru": []},
    )

    result = find_quorum_slots(
        config=config,
        attendees=attendees,
        preferred=preferred,
        duration=timedelta(hours=1),
        max_days=1,
        step=timedelta(minutes=60),
        max_items=50,
        source="freebusy",
        workers=1,
        max_results=3,
        verify_top_n=0,
        verify_calendar=False,
        latest_allowed=latest_allowed,
        raise_if_empty=False,
    )

    assert result["candidates"] == []
    assert result["search_mode"] == "empty"


def test_coverage_ratios_uses_weights() -> None:
    attendees = ["a@turbo-don.ru", "b@turbo-don.ru", "c@turbo-don.ru", "d@turbo-don.ru"]
    weights = {
        "a@turbo-don.ru": 3.0,
        "b@turbo-don.ru": 3.0,
        "c@turbo-don.ru": 1.0,
        "d@turbo-don.ru": 1.0,
    }
    weighted, flat = coverage_ratios(["a@turbo-don.ru", "b@turbo-don.ru"], attendees, weights)
    assert flat == 0.5
    assert weighted == 0.75


def test_partition_attendees_at_slot() -> None:
    config = _config()
    tz = ZoneInfo("Europe/Moscow")
    slot_start = datetime(2026, 6, 19, 10, 0, tzinfo=tz)
    busy = {
        "a@turbo-don.ru": [
            (
                datetime(2026, 6, 19, 10, 0, tzinfo=tz),
                datetime(2026, 6, 19, 11, 0, tzinfo=tz),
            )
        ],
        "b@turbo-don.ru": [],
    }
    free, busy_list = partition_attendees_at_slot(
        slot_start,
        timedelta(minutes=30),
        attendees=["a@turbo-don.ru", "b@turbo-don.ru"],
        busy_by_attendee=busy,
        config=config,
    )
    assert free == ["b@turbo-don.ru"]
    assert busy_list == ["a@turbo-don.ru"]


def test_conflicting_calendar_items_at_slot_returns_subject() -> None:
    config = _config()
    tz = ZoneInfo("Europe/Moscow")
    slot_start = datetime(2026, 7, 9, 8, 45, tzinfo=tz)
    event = SimpleNamespace(
        subject="Sync отдела",
        start=datetime(2026, 7, 9, 8, 30, tzinfo=tz),
        end=datetime(2026, 7, 9, 9, 30, tzinfo=tz),
        legacy_free_busy_status="Busy",
        is_cancelled=False,
        organizer=SimpleNamespace(email_address="boss@turbo-don.ru"),
    )
    records = conflicting_calendar_items_at_slot(
        [event],
        slot_start,
        timedelta(hours=2),
        config,
    )
    assert len(records) == 1
    assert records[0]["event_subject"] == "Sync отдела"
    assert records[0]["organizer"] == "boss@turbo-don.ru"
    assert records[0]["source"] == "calendar"


def test_movability_reason_for_interval_without_subject() -> None:
    assert movability_reason(busy_type="Busy", subject="", source="interval") == "unknown_interval"
    assert movability_reason(busy_type="Tentative", subject="", source="freebusy") == "tentative"


def test_dedupe_conflict_records() -> None:
    records = [
        {"event_start": "a", "event_end": "b", "event_subject": "X", "source": "calendar"},
        {"event_start": "a", "event_end": "b", "event_subject": None, "source": "interval"},
        {"event_start": "c", "event_end": "d", "event_subject": None, "source": "interval"},
    ]
    deduped = dedupe_conflict_records(records)
    assert len(deduped) == 2
    assert deduped[0]["event_subject"] == "X"
    assert deduped[1]["event_subject"] is None


def test_suggest_reschedule_window_avoids_reserved_slot() -> None:
    config = _config()
    tz = ZoneInfo("Europe/Moscow")
    reserved = (
        datetime(2026, 7, 14, 9, 0, tzinfo=tz),
        datetime(2026, 7, 14, 12, 0, tzinfo=tz),
    )
    event = (
        datetime(2026, 7, 14, 9, 0, tzinfo=tz),
        datetime(2026, 7, 14, 9, 30, tzinfo=tz),
    )
    busy = [
        (
            datetime(2026, 7, 14, 11, 0, tzinfo=tz),
            datetime(2026, 7, 14, 11, 30, tzinfo=tz),
        ),
    ]
    hint = suggest_reschedule_window(
        event_start=event[0],
        event_end=event[1],
        busy_intervals=busy,
        config=config,
        step=timedelta(minutes=15),
        search_end=datetime(2026, 7, 15, 18, 0, tzinfo=tz),
        reserved_slot=reserved,
    )
    assert hint is not None
    assert hint[0] >= datetime(2026, 7, 14, 12, 0, tzinfo=tz)


def test_calendar_item_attendee_emails_collects_required_and_organizer() -> None:
    def attendee(email: str) -> SimpleNamespace:
        return SimpleNamespace(mailbox=SimpleNamespace(email_address=email))

    item = SimpleNamespace(
        required_attendees=[
            attendee("a@turbo-don.ru"),
            attendee("B@turbo-don.ru"),
        ],
        optional_attendees=[attendee("c@turbo-don.ru")],
        organizer=SimpleNamespace(email_address="boss@turbo-don.ru"),
    )
    emails = calendar_item_attendee_emails(item)
    assert emails == [
        "a@turbo-don.ru",
        "b@turbo-don.ru",
        "c@turbo-don.ru",
        "boss@turbo-don.ru",
    ]


def _calendar_item(
    *,
    subject: str,
    start: datetime,
    end: datetime,
    attendees: list[str],
    busy_type: str = "Busy",
    organizer: str = "boss@turbo-don.ru",
) -> SimpleNamespace:
    def attendee(email: str) -> SimpleNamespace:
        return SimpleNamespace(mailbox=SimpleNamespace(email_address=email))

    return SimpleNamespace(
        subject=subject,
        start=start,
        end=end,
        is_cancelled=False,
        legacy_free_busy_status=busy_type,
        required_attendees=[attendee(email) for email in attendees],
        optional_attendees=[],
        organizer=SimpleNamespace(email_address=organizer),
    )


def test_find_company_calendar_reschedule_candidates_filters_by_attendee(monkeypatch) -> None:
    config = _config()
    tz = ZoneInfo("Europe/Moscow")
    target_start = datetime(2026, 7, 14, 10, 0, tzinfo=tz)
    matching = _calendar_item(
        subject="Совещание A",
        start=datetime(2026, 7, 14, 10, 0, tzinfo=tz),
        end=datetime(2026, 7, 14, 11, 0, tzinfo=tz),
        attendees=["a@turbo-don.ru", "calendar@turbo-don.ru"],
    )
    unrelated = _calendar_item(
        subject="Совещание B",
        start=datetime(2026, 7, 14, 10, 0, tzinfo=tz),
        end=datetime(2026, 7, 14, 11, 0, tzinfo=tz),
        attendees=["x@turbo-don.ru"],
    )

    def fake_read(_config, _owner, *, range_start, range_end, max_items):
        del range_start, range_end, max_items
        return [matching, unrelated]

    monkeypatch.setattr(
        "app.tools.Outlook.slot_search.search.read_calendar_items_in_range",
        fake_read,
    )
    monkeypatch.setattr(
        "app.tools.Outlook.slot_search.search.fetch_busy_intervals_freebusy",
        lambda *_args, **_kwargs: {"a@turbo-don.ru": []},
    )
    monkeypatch.setattr(
        "app.tools.Outlook.slot_search.search.suggest_reschedule_window",
        lambda **_kwargs: (
            datetime(2026, 7, 14, 13, 0, tzinfo=tz),
            datetime(2026, 7, 14, 14, 0, tzinfo=tz),
        ),
    )

    result = find_company_calendar_reschedule_candidates(
        attendee_emails=["a@turbo-don.ru"],
        planned_start=target_start,
        duration=timedelta(minutes=60),
        max_days=1,
        config=config,
    )

    assert result["company_calendar"] == "calendar@turbo-don.ru"
    assert len(result["candidates"]) == 1
    candidate = result["candidates"][0]
    assert candidate["event_subject"] == "Совещание A"
    assert candidate["source"] == "company_calendar"
    assert candidate["email"] == "a@turbo-don.ru"
    assert "a@turbo-don.ru" in candidate["event_attendees"]


def test_find_company_calendar_reschedule_candidates_ranks_tentative_first(monkeypatch) -> None:
    config = _config()
    tz = ZoneInfo("Europe/Moscow")
    target_start = datetime(2026, 7, 14, 10, 0, tzinfo=tz)
    busy_item = _calendar_item(
        subject="Busy meeting",
        start=datetime(2026, 7, 14, 10, 0, tzinfo=tz),
        end=datetime(2026, 7, 14, 11, 0, tzinfo=tz),
        attendees=["a@turbo-don.ru"],
        busy_type="Busy",
    )
    tentative_item = _calendar_item(
        subject="Tentative meeting",
        start=datetime(2026, 7, 14, 10, 30, tzinfo=tz),
        end=datetime(2026, 7, 14, 11, 30, tzinfo=tz),
        attendees=["a@turbo-don.ru"],
        busy_type="Tentative",
    )

    monkeypatch.setattr(
        "app.tools.Outlook.slot_search.search.read_calendar_items_in_range",
        lambda *_args, **_kwargs: [busy_item, tentative_item],
    )
    monkeypatch.setattr(
        "app.tools.Outlook.slot_search.search.fetch_busy_intervals_freebusy",
        lambda *_args, **_kwargs: {"a@turbo-don.ru": []},
    )
    monkeypatch.setattr(
        "app.tools.Outlook.slot_search.search.suggest_reschedule_window",
        lambda **_kwargs: (
            datetime(2026, 7, 14, 13, 0, tzinfo=tz),
            datetime(2026, 7, 14, 14, 0, tzinfo=tz),
        ),
    )

    result = find_company_calendar_reschedule_candidates(
        attendee_emails=["a@turbo-don.ru"],
        planned_start=target_start,
        duration=timedelta(minutes=60),
        max_days=1,
        config=config,
    )

    subjects = [item["event_subject"] for item in result["candidates"]]
    assert subjects[0] == "Tentative meeting"
    assert result["candidates"][0]["movability"] == "high"


def test_suggest_reschedule_window_checks_all_meeting_attendees(monkeypatch) -> None:
    config = _config()
    tz = ZoneInfo("Europe/Moscow")
    event = (
        datetime(2026, 7, 14, 10, 0, tzinfo=tz),
        datetime(2026, 7, 14, 11, 0, tzinfo=tz),
    )
    owner = "a@turbo-don.ru"
    other = "b@turbo-don.ru"
    other_busy = (
        datetime(2026, 7, 14, 11, 0, tzinfo=tz),
        datetime(2026, 7, 14, 11, 30, tzinfo=tz),
    )

    def fake_fetch(_config, attendees, _start, _end):
        assert set(attendees) == {owner, other}
        return {
            owner: [],
            other: [other_busy],
        }

    monkeypatch.setattr(
        "app.tools.Outlook.slot_search.conflicts.fetch_busy_intervals_freebusy",
        fake_fetch,
    )

    hint = suggest_reschedule_window(
        event_start=event[0],
        event_end=event[1],
        busy_intervals=[],
        config=config,
        step=timedelta(minutes=30),
        search_end=datetime(2026, 7, 14, 18, 0, tzinfo=tz),
        owner_email=owner,
        meeting_attendees=[owner, other],
    )

    assert hint is not None
    assert hint[0] >= datetime(2026, 7, 14, 13, 0, tzinfo=tz)


def test_suggest_reschedule_window_normalizes_naive_search_end() -> None:
    config = _config()
    tz = ZoneInfo("Europe/Moscow")
    event = (
        datetime(2026, 7, 14, 10, 0, tzinfo=tz),
        datetime(2026, 7, 14, 11, 0, tzinfo=tz),
    )
    hint = suggest_reschedule_window(
        event_start=event[0],
        event_end=event[1],
        busy_intervals=[],
        config=config,
        step=timedelta(minutes=15),
        search_end=datetime(2026, 7, 15, 18, 0),
    )
    assert hint is not None


def test_suggest_reschedule_window_ignores_resource_calendar_for_group_check(
    monkeypatch,
) -> None:
    config = _config()
    tz = ZoneInfo("Europe/Moscow")
    owner = "a@turbo-don.ru"
    event = (
        datetime(2026, 7, 14, 9, 0, tzinfo=tz),
        datetime(2026, 7, 14, 9, 30, tzinfo=tz),
    )
    reserved = (
        datetime(2026, 7, 14, 9, 0, tzinfo=tz),
        datetime(2026, 7, 14, 12, 0, tzinfo=tz),
    )

    def fail_fetch(*_args, **_kwargs):
        raise AssertionError("group fetch should not run for resource-only attendees")

    monkeypatch.setattr(
        "app.tools.Outlook.slot_search.conflicts.fetch_busy_intervals_freebusy",
        fail_fetch,
    )

    hint = suggest_reschedule_window(
        event_start=event[0],
        event_end=event[1],
        busy_intervals=[],
        config=config,
        step=timedelta(minutes=15),
        search_end=datetime(2026, 7, 15, 18, 0, tzinfo=tz),
        reserved_slot=reserved,
        owner_email=owner,
        meeting_attendees=["calendar@turbo-don.ru"],
    )
    assert hint is not None
    assert hint[0] >= datetime(2026, 7, 14, 12, 0, tzinfo=tz)


def test_suggest_reschedule_window_falls_back_when_group_has_no_slot(monkeypatch) -> None:
    config = _config()
    tz = ZoneInfo("Europe/Moscow")
    owner = "a@turbo-don.ru"
    other = "b@turbo-don.ru"
    event = (
        datetime(2026, 7, 14, 10, 0, tzinfo=tz),
        datetime(2026, 7, 14, 11, 0, tzinfo=tz),
    )
    owner_busy = (
        datetime(2026, 7, 14, 11, 0, tzinfo=tz),
        datetime(2026, 7, 14, 11, 30, tzinfo=tz),
    )

    def fake_fetch(_config, attendees, _start, _end):
        return {
            owner: [owner_busy],
            other: [
                (
                    datetime(2026, 7, 14, 11, 0, tzinfo=tz),
                    datetime(2026, 7, 14, 18, 0, tzinfo=tz),
                ),
            ],
        }

    monkeypatch.setattr(
        "app.tools.Outlook.slot_search.conflicts.fetch_busy_intervals_freebusy",
        fake_fetch,
    )

    hint = suggest_reschedule_window(
        event_start=event[0],
        event_end=event[1],
        busy_intervals=[owner_busy],
        config=config,
        step=timedelta(minutes=30),
        search_end=datetime(2026, 7, 14, 18, 0, tzinfo=tz),
        owner_email=owner,
        meeting_attendees=[owner, other],
    )

    assert hint is not None
    assert hint[0] >= datetime(2026, 7, 14, 13, 0, tzinfo=tz)


def test_attach_reschedule_hints_avoids_overlapping_alternatives() -> None:
    config = _config()
    tz = ZoneInfo("Europe/Moscow")
    slot_start = datetime(2026, 7, 14, 9, 0, tzinfo=tz)
    slot_end = datetime(2026, 7, 14, 12, 0, tzinfo=tz)
    records = [
        {
            "event_start": datetime(2026, 7, 14, 9, 0, tzinfo=tz).isoformat(),
            "event_end": datetime(2026, 7, 14, 9, 30, tzinfo=tz).isoformat(),
            "event_subject": "Meeting A",
            "busy_type": "Busy",
            "source": "calendar",
            "event_attendees": ["calendar@turbo-don.ru"],
        },
        {
            "event_start": datetime(2026, 7, 14, 10, 0, tzinfo=tz).isoformat(),
            "event_end": datetime(2026, 7, 14, 11, 0, tzinfo=tz).isoformat(),
            "event_subject": "Meeting B",
            "busy_type": "Busy",
            "source": "calendar",
            "event_attendees": ["calendar@turbo-don.ru"],
        },
    ]

    conflicts = attach_reschedule_hints(
        records,
        owner_email="a@turbo-don.ru",
        busy_intervals=[],
        config=config,
        step=timedelta(minutes=15),
        search_end=datetime(2026, 7, 15, 18, 0, tzinfo=tz),
        reserved_slot=(slot_start, slot_end),
    )

    hints = [
        (
            datetime.fromisoformat(item["reschedule_hint_start"]),
            datetime.fromisoformat(item["reschedule_hint_end"]),
        )
        for item in conflicts
        if item.get("reschedule_hint_start") and item.get("reschedule_hint_end")
    ]
    assert len(hints) == 2
    assert hints[0] == (
        datetime(2026, 7, 14, 13, 0, tzinfo=tz),
        datetime(2026, 7, 14, 13, 30, tzinfo=tz),
    )
    assert hints[1] == (
        datetime(2026, 7, 14, 13, 30, tzinfo=tz),
        datetime(2026, 7, 14, 14, 30, tzinfo=tz),
    )
    assert not intervals_overlap(hints[0][0], hints[0][1], hints[1][0], hints[1][1])


def test_build_slot_participant_details_marks_free_and_busy(monkeypatch) -> None:
    config = _config()
    tz = ZoneInfo("Europe/Moscow")
    slot_start = datetime(2026, 7, 9, 8, 45, tzinfo=tz)
    slot_end = datetime(2026, 7, 9, 10, 45, tzinfo=tz)
    busy_block = (
        datetime(2026, 7, 9, 8, 30, tzinfo=tz),
        datetime(2026, 7, 9, 9, 30, tzinfo=tz),
    )
    calendar_event = SimpleNamespace(
        subject="Согласование бюджета",
        start=busy_block[0],
        end=busy_block[1],
        legacy_free_busy_status="Busy",
        is_cancelled=False,
        organizer=None,
    )

    monkeypatch.setattr(
        "app.tools.Outlook.slot_search.api.busy_intervals_and_events_from_freebusy",
        lambda *_args, **_kwargs: (
            {
                "a@turbo-don.ru": [busy_block],
                "b@turbo-don.ru": [],
            },
            {},
        ),
    )
    monkeypatch.setattr(
        "app.tools.Outlook.slot_search.api.read_calendar_items_in_range",
        lambda *_args, **_kwargs: [calendar_event],
    )

    result = build_slot_participant_details(
        config=config,
        attendees=[
            {"fio": "A", "email": "a@turbo-don.ru", "role": "manager"},
            {"fio": "B", "email": "b@turbo-don.ru", "role": "participant"},
        ],
        slot_start=slot_start,
        slot_end=slot_end,
    )

    assert result["duration_minutes"] == 120
    by_email = {item["email"]: item for item in result["participants"]}
    assert by_email["b@turbo-don.ru"]["status"] == "free"
    assert by_email["a@turbo-don.ru"]["status"] == "busy"
    assert by_email["a@turbo-don.ru"]["blocking_events"][0]["event_subject"] == "Согласование бюджета"


def test_build_slot_participant_details_skips_company_calendar_by_default(monkeypatch) -> None:
    config = _config()
    tz = ZoneInfo("Europe/Moscow")
    slot_start = datetime(2026, 7, 9, 8, 45, tzinfo=tz)
    slot_end = datetime(2026, 7, 9, 10, 45, tzinfo=tz)
    busy_block = (
        datetime(2026, 7, 9, 8, 30, tzinfo=tz),
        datetime(2026, 7, 9, 9, 30, tzinfo=tz),
    )
    company_calls: list[str] = []

    def _read_calendar(_config, mailbox, **_kwargs):
        company_calls.append(mailbox)
        return []

    monkeypatch.setattr(
        "app.tools.Outlook.slot_search.api.busy_intervals_and_events_from_freebusy",
        lambda *_args, **_kwargs: ({"a@turbo-don.ru": [busy_block]}, {}),
    )
    monkeypatch.setattr(
        "app.tools.Outlook.slot_search.api.read_calendar_items_in_range",
        _read_calendar,
    )

    build_slot_participant_details(
        config=config,
        attendees=[{"fio": "A", "email": "a@turbo-don.ru", "role": "manager"}],
        slot_start=slot_start,
        slot_end=slot_end,
    )

    assert company_calls == ["a@turbo-don.ru"]
    assert config.company_calendar not in company_calls


def test_build_slot_participant_details_reads_company_calendar_when_requested(monkeypatch) -> None:
    config = _config()
    tz = ZoneInfo("Europe/Moscow")
    slot_start = datetime(2026, 7, 9, 8, 45, tzinfo=tz)
    slot_end = datetime(2026, 7, 9, 10, 45, tzinfo=tz)
    busy_block = (
        datetime(2026, 7, 9, 8, 0, tzinfo=tz),
        datetime(2026, 7, 9, 12, 0, tzinfo=tz),
    )
    company_calls: list[str] = []

    def _read_calendar(_config, mailbox, **_kwargs):
        company_calls.append(mailbox)
        return []

    monkeypatch.setattr(
        "app.tools.Outlook.slot_search.api.busy_intervals_and_events_from_freebusy",
        lambda *_args, **_kwargs: ({"a@turbo-don.ru": [busy_block]}, {}),
    )
    monkeypatch.setattr(
        "app.tools.Outlook.slot_search.api.read_calendar_items_in_range",
        _read_calendar,
    )

    build_slot_participant_details(
        config=config,
        attendees=[{"fio": "A", "email": "a@turbo-don.ru", "role": "manager"}],
        slot_start=slot_start,
        slot_end=slot_end,
        include_company_calendar=True,
    )

    assert config.company_calendar in company_calls


def test_build_slot_participant_details_skips_company_calendar_when_all_free(monkeypatch) -> None:
    config = _config()
    tz = ZoneInfo("Europe/Moscow")
    slot_start = datetime(2026, 7, 9, 8, 45, tzinfo=tz)
    slot_end = datetime(2026, 7, 9, 10, 45, tzinfo=tz)
    company_calls: list[str] = []

    def _read_calendar(_config, mailbox, **_kwargs):
        company_calls.append(mailbox)
        return []

    monkeypatch.setattr(
        "app.tools.Outlook.slot_search.api.busy_intervals_and_events_from_freebusy",
        lambda *_args, **_kwargs: ({"a@turbo-don.ru": []}, {}),
    )
    monkeypatch.setattr(
        "app.tools.Outlook.slot_search.api.read_calendar_items_in_range",
        _read_calendar,
    )

    build_slot_participant_details(
        config=config,
        attendees=[{"fio": "A", "email": "a@turbo-don.ru", "role": "manager"}],
        slot_start=slot_start,
        slot_end=slot_end,
        include_company_calendar=True,
    )

    assert company_calls == []


def test_build_slot_participant_details_uses_company_calendar_for_busy_subject(monkeypatch) -> None:
    config = _config()
    tz = ZoneInfo("Europe/Moscow")
    slot_start = datetime(2026, 7, 14, 11, 30, tzinfo=tz)
    slot_end = datetime(2026, 7, 14, 12, 0, tzinfo=tz)
    busy_block = (
        datetime(2026, 7, 14, 8, 0, tzinfo=tz),
        datetime(2026, 7, 14, 17, 0, tzinfo=tz),
    )
    company_item = _calendar_item(
        subject="Project meeting",
        start=datetime(2026, 7, 14, 11, 0, tzinfo=tz),
        end=datetime(2026, 7, 14, 12, 30, tzinfo=tz),
        attendees=["a@turbo-don.ru"],
    )

    def _read_calendar(_config, mailbox, **_kwargs):
        if mailbox == config.company_calendar:
            return [company_item]
        raise RuntimeError(
            "Calendar folder not found. Check Reviewer/Delegate rights or specify another --owner."
        )

    monkeypatch.setattr(
        "app.tools.Outlook.slot_search.api.busy_intervals_and_events_from_freebusy",
        lambda *_args, **_kwargs: ({"a@turbo-don.ru": [busy_block]}, {}),
    )
    monkeypatch.setattr(
        "app.tools.Outlook.slot_search.api.read_calendar_items_in_range",
        _read_calendar,
    )

    result = build_slot_participant_details(
        config=config,
        attendees=[{"fio": "A", "email": "a@turbo-don.ru", "role": "initiator"}],
        slot_start=slot_start,
        slot_end=slot_end,
        include_company_calendar=True,
    )

    participant = result["participants"][0]
    assert participant["status"] == "busy"
    assert participant["calendar_access_error"] is None
    assert participant["blocking_events"][0]["event_subject"] == "Project meeting"
    assert participant["blocking_events"][0]["source"] == "company_calendar"


def test_preliminary_slot_impact_prefers_lighter_busy_set() -> None:
    weights = {
        "director@turbo-don.ru": 3.0,
        "staff@turbo-don.ru": 1.0,
    }
    heavy = preliminary_slot_impact(
        score_ratio=0.5,
        busy_attendees=["director@turbo-don.ru"],
        required=["director@turbo-don.ru"],
        required_ok=False,
        attendee_weights=weights,
    )
    light = preliminary_slot_impact(
        score_ratio=0.875,
        busy_attendees=["staff@turbo-don.ru"],
        required=["director@turbo-don.ru"],
        required_ok=True,
        attendee_weights=weights,
    )
    assert light < heavy


def test_slot_impact_score_accounts_for_movability_and_role() -> None:
    weights = {
        "director@turbo-don.ru": 3.0,
        "staff@turbo-don.ru": 1.0,
    }
    director_conflict = slot_impact_score(
        weighted_coverage_ratio=0.5,
        required_ok=False,
        busy_attendees=["director@turbo-don.ru"],
        required=["director@turbo-don.ru"],
        conflicts=[{"email": "director@turbo-don.ru", "movability": "low"}],
        attendee_weights=weights,
    )
    staff_conflict = slot_impact_score(
        weighted_coverage_ratio=0.875,
        required_ok=True,
        busy_attendees=["staff@turbo-don.ru"],
        required=["director@turbo-don.ru"],
        conflicts=[{"email": "staff@turbo-don.ru", "movability": "high"}],
        attendee_weights=weights,
    )
    assert staff_conflict < director_conflict
