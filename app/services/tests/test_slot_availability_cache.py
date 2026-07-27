from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.services.slot_availability_cache import (
    get_availability_snapshot,
    serialize_busy_snapshot,
    slot_within_snapshot_window,
    snapshot_from_payload,
    store_availability_snapshot,
    trim_snapshot_for_cache,
    _entries,
    _lock,
)


def test_store_and_reuse_availability_snapshot() -> None:
    tz = ZoneInfo("Europe/Moscow")
    window_start = datetime(2026, 7, 14, 8, 0, tzinfo=tz)
    window_end = datetime(2026, 8, 13, 18, 0, tzinfo=tz)
    busy_block = (
        datetime(2026, 7, 20, 10, 0, tzinfo=tz),
        datetime(2026, 7, 20, 12, 0, tzinfo=tz),
    )
    payload = serialize_busy_snapshot(
        memo_ref_key="abc",
        attendee_emails=["a@turbo-don.ru", "b@turbo-don.ru"],
        window_start=window_start,
        window_end=window_end,
        busy_by_attendee={"a@turbo-don.ru": [busy_block]},
    )
    cache_id = store_availability_snapshot(payload)
    assert cache_id

    snapshot = get_availability_snapshot(cache_id)
    assert snapshot is not None
    assert snapshot.memo_ref_key == "abc"
    assert snapshot.busy_by_attendee["a@turbo-don.ru"][0] == busy_block

    slot_start = datetime(2026, 7, 20, 11, 0, tzinfo=tz)
    slot_end = datetime(2026, 7, 20, 11, 30, tzinfo=tz)
    assert slot_within_snapshot_window(snapshot, slot_start=slot_start, slot_end=slot_end)


def test_snapshot_from_payload_roundtrip() -> None:
    tz = ZoneInfo("Europe/Moscow")
    payload = serialize_busy_snapshot(
        memo_ref_key="memo-1",
        attendee_emails=["user@turbo-don.ru"],
        window_start=datetime(2026, 7, 14, 9, 0, tzinfo=tz),
        window_end=datetime(2026, 8, 14, 9, 0, tzinfo=tz),
        busy_by_attendee={},
    )
    snapshot = snapshot_from_payload(payload)
    assert snapshot is not None
    assert snapshot.attendee_emails == ("user@turbo-don.ru",)


def test_trim_snapshot_for_cache_limits_window_to_seven_days() -> None:
    tz = ZoneInfo("Europe/Moscow")
    window_start = datetime(2026, 6, 19, 8, 0, tzinfo=tz)
    window_end = datetime(2026, 7, 19, 18, 0, tzinfo=tz)
    inside = (
        datetime(2026, 6, 20, 10, 0, tzinfo=tz),
        datetime(2026, 6, 20, 12, 0, tzinfo=tz),
    )
    outside = (
        datetime(2026, 7, 10, 10, 0, tzinfo=tz),
        datetime(2026, 7, 10, 12, 0, tzinfo=tz),
    )
    payload = serialize_busy_snapshot(
        memo_ref_key="abc",
        attendee_emails=["a@turbo-don.ru"],
        window_start=window_start,
        window_end=window_end,
        busy_by_attendee={"a@turbo-don.ru": [inside, outside]},
    )

    trimmed = trim_snapshot_for_cache(payload, max_window_days=7)
    snapshot = snapshot_from_payload(trimmed)
    assert snapshot is not None
    assert snapshot.window_end == window_start + timedelta(days=7)
    assert snapshot.busy_by_attendee["a@turbo-don.ru"] == [inside]


def test_availability_cache_expires_after_ttl() -> None:
    tz = ZoneInfo("Europe/Moscow")
    payload = serialize_busy_snapshot(
        memo_ref_key="abc",
        attendee_emails=["a@turbo-don.ru"],
        window_start=datetime(2026, 7, 14, 8, 0, tzinfo=tz),
        window_end=datetime(2026, 7, 21, 18, 0, tzinfo=tz),
        busy_by_attendee={},
    )
    cache_id = store_availability_snapshot(payload)
    assert cache_id

    with _lock:
        stored_at, snapshot = _entries[cache_id]
        _entries[cache_id] = (
            stored_at - timedelta(minutes=11),
            snapshot,
        )

    assert get_availability_snapshot(cache_id) is None


def test_find_meeting_slot_output_preserves_availability_snapshot() -> None:
    from app.tools.outlook_tools import FindMeetingSlotOutput

    snapshot = {
        "memo_ref_key": "abc",
        "attendee_emails": ["a@turbo-don.ru"],
        "window_start": "2026-07-14T08:00:00+03:00",
        "window_end": "2026-07-21T18:00:00+03:00",
        "busy_by_attendee": {},
    }
    raw = {
        "preferred": "2026-07-20T10:00:00+03:00",
        "slot_start": "2026-07-20T10:24:00+03:00",
        "slot_end": "2026-07-20T10:54:00+03:00",
        "duration_minutes": 30,
        "attendees": ["a@turbo-don.ru"],
        "checked_candidates": 1,
        "search_until": "2026-08-19T10:00:00+03:00",
        "availability_source": "freebusy",
        "availability_snapshot": snapshot,
    }

    output = FindMeetingSlotOutput.model_validate(raw)
    dumped = output.model_dump(mode="json")

    assert dumped.get("availability_snapshot") == snapshot
