from __future__ import annotations

import uuid
from datetime import date, datetime, time, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from app.models.enums import (
    MeetingRegistryStage,
    ScheduledMeetingFrequency,
    ScheduledMeetingStatus,
    ScheduledMeetingType,
)
from app.services.scheduled_meeting_occurrences import SeriesOccurrence
from app.services.scheduled_meeting_registry_sync import ScheduledMeetingRegistrySyncService


def _series_stub(*, series_id: uuid.UUID) -> SimpleNamespace:
    department = SimpleNamespace(id=uuid.uuid4(), name="Главный инженер")
    return SimpleNamespace(
        id=series_id,
        title="Технический совет",
        meeting_type=ScheduledMeetingType.PLANNED,
        status=ScheduledMeetingStatus.PLANNED,
        time_local=time(9, 0),
        duration_minutes=30,
        frequency=ScheduledMeetingFrequency.DAILY,
        interval=1,
        monthly_mode=None,
        day_of_month=None,
        weekday=None,
        weekday_position=None,
        series_start_date=date(2026, 7, 15),
        series_end_date=date(2026, 7, 17),
        recurrence_label="ежедневно, 9:00",
        outlook_series_id="master-1",
        outlook_changekey="ck-master",
        outlook_meeting_url="https://example.test/meeting",
        participants=[
            SimpleNamespace(
                sort_order=0,
                position=department,
            )
        ],
    )


def _occurrence(
    occurrence_date: date,
    *,
    item_id: str,
) -> SeriesOccurrence:
    tz = ZoneInfo("Europe/Moscow")
    slot_start = datetime.combine(occurrence_date, time(9, 0), tzinfo=tz)
    slot_end = datetime.combine(occurrence_date, time(9, 30), tzinfo=tz)
    return SeriesOccurrence(
        occurrence_date=occurrence_date,
        slot_start=slot_start,
        slot_end=slot_end,
        outlook_item_id=item_id,
        outlook_changekey=f"ck-{item_id}",
        subject="Технический совет",
        is_cancelled=False,
        source="rule",
    )


@pytest.mark.asyncio
async def test_sync_series_card_creates_registry_entry() -> None:
    db = AsyncMock()
    series_id = uuid.uuid4()
    series = _series_stub(series_id=series_id)

    scalars = MagicMock()
    execute_result = MagicMock()
    execute_result.scalar_one_or_none.return_value = series
    db.execute = AsyncMock(return_value=execute_result)
    db.add = MagicMock()
    db.flush = AsyncMock()

    service = ScheduledMeetingRegistrySyncService(db)
    occurrences = [
        _occurrence(date(2026, 7, 15), item_id="occ-1"),
        _occurrence(date(2026, 7, 16), item_id="occ-2"),
    ]

    with (
        patch.object(service, "_now", return_value=datetime(2026, 7, 15, 8, 0, tzinfo=ZoneInfo("Europe/Moscow"))),
        patch.object(service, "_resolve_occurrences", AsyncMock(return_value=(occurrences, "rule"))),
        patch(
            "app.services.scheduled_meeting_registry_sync.MeetingRegistryService.get_entry_by_scheduled_meeting_id",
            AsyncMock(return_value=None),
        ),
    ):
        result = await service.sync_series_card(series_id)

    assert result.action == "created"
    assert result.occurrence_date == date(2026, 7, 15)
    assert db.add.call_count >= 2


@pytest.mark.asyncio
async def test_sync_series_card_rolls_past_occurrence() -> None:
    db = AsyncMock()
    series_id = uuid.uuid4()
    series = _series_stub(series_id=series_id)
    tz = ZoneInfo("Europe/Moscow")
    entry = SimpleNamespace(
        id=uuid.uuid4(),
        memo_ref_key=str(uuid.uuid4()),
        title=series.title,
        subject=series.title,
        participants=["Главный инженер"],
        participants_count=1,
        slot_start=datetime(2026, 7, 15, 9, 0, tzinfo=tz),
        slot_end=datetime(2026, 7, 15, 9, 30, tzinfo=tz),
        stage=MeetingRegistryStage.SCHEDULED,
        series_occurrence_date=date(2026, 7, 15),
        outlook_item_id="occ-1",
        outlook_changekey="ck-1",
        outlook_meeting_url=series.outlook_meeting_url,
        payload={"source": "scheduled_series"},
        scheduled_meeting_id=series_id,
    )

    scalars = MagicMock()
    execute_result = MagicMock()
    execute_result.scalar_one_or_none.return_value = series
    db.execute = AsyncMock(return_value=execute_result)
    db.flush = AsyncMock()

    service = ScheduledMeetingRegistrySyncService(db)
    occurrences = [
        _occurrence(date(2026, 7, 15), item_id="occ-1"),
        _occurrence(date(2026, 7, 16), item_id="occ-2"),
    ]

    with (
        patch.object(service, "_now", return_value=datetime(2026, 7, 16, 8, 0, tzinfo=tz)),
        patch.object(service, "_resolve_occurrences", AsyncMock(return_value=(occurrences, "rule"))),
        patch(
            "app.services.scheduled_meeting_registry_sync.MeetingRegistryService.get_entry_by_scheduled_meeting_id",
            AsyncMock(return_value=entry),
        ),
        patch(
            "app.services.scheduled_meeting_registry_sync.MeetingRegistryService.append_event",
            AsyncMock(),
        ),
    ):
        result = await service.sync_series_card(series_id)

    assert result.action == "rolled"
    assert entry.series_occurrence_date == date(2026, 7, 16)
    assert entry.outlook_item_id == "occ-2"


@pytest.mark.asyncio
async def test_sync_series_card_skips_unplanned_series() -> None:
    db = AsyncMock()
    series_id = uuid.uuid4()
    series = _series_stub(series_id=series_id)
    series.status = ScheduledMeetingStatus.CREATED

    scalars = MagicMock()
    execute_result = MagicMock()
    execute_result.scalar_one_or_none.return_value = series
    db.execute = AsyncMock(return_value=execute_result)

    result = await ScheduledMeetingRegistrySyncService(db).sync_series_card(series_id)
    assert result.action == "skipped"


@pytest.mark.asyncio
async def test_sync_series_card_preserves_ad_hoc_participants_on_roll() -> None:
    db = AsyncMock()
    series_id = uuid.uuid4()
    series = _series_stub(series_id=series_id)
    tz = ZoneInfo("Europe/Moscow")
    entry = SimpleNamespace(
        id=uuid.uuid4(),
        memo_ref_key=str(uuid.uuid4()),
        title=series.title,
        subject=series.title,
        participants=["Главный инженер", "Кондratyuk M.B."],
        participants_count=2,
        slot_start=datetime(2026, 7, 15, 9, 0, tzinfo=tz),
        slot_end=datetime(2026, 7, 15, 9, 30, tzinfo=tz),
        stage=MeetingRegistryStage.SCHEDULED,
        series_occurrence_date=date(2026, 7, 15),
        outlook_item_id="occ-1",
        outlook_changekey="ck-1",
        outlook_meeting_url=series.outlook_meeting_url,
        payload={
            "source": "scheduled_series",
            "attendees": ["chief@turbo-don.ru", "extra@turbo-don.ru"],
            "occurrence_participant_names": [
                "Главный инженер",
                "Кондratyuk M.B.",
            ],
        },
        scheduled_meeting_id=series_id,
    )

    execute_result = MagicMock()
    execute_result.scalar_one_or_none.return_value = series
    db.execute = AsyncMock(return_value=execute_result)
    db.flush = AsyncMock()

    service = ScheduledMeetingRegistrySyncService(db)
    occurrences = [
        _occurrence(date(2026, 7, 15), item_id="occ-1"),
        _occurrence(date(2026, 7, 16), item_id="occ-2"),
    ]

    with (
        patch.object(service, "_now", return_value=datetime(2026, 7, 16, 8, 0, tzinfo=tz)),
        patch.object(service, "_resolve_occurrences", AsyncMock(return_value=(occurrences, "rule"))),
        patch(
            "app.services.scheduled_meeting_registry_sync.MeetingRegistryService.get_entry_by_scheduled_meeting_id",
            AsyncMock(return_value=entry),
        ),
        patch(
            "app.services.scheduled_meeting_registry_sync.MeetingRegistryService.append_event",
            AsyncMock(),
        ),
    ):
        result = await service.sync_series_card(series_id)

    assert result.action == "rolled"
    assert entry.participants == ["Главный инженер", "Кондratyuk M.B."]
    assert entry.participants_count == 2


def test_registry_participants_for_display_uses_pending_add() -> None:
    from types import SimpleNamespace

    from app.services.meeting_attendees import registry_participants_for_display

    entry = SimpleNamespace(
        participants=["Директор по развитию"],
        payload={
            "pending_add": {
                "participants": [
                    "Директор по развитию",
                    "Кондratyuk M.B.",
                ],
            },
        },
    )
    assert registry_participants_for_display(entry) == [
        "Директор по развитию",
        "Кондratyuk M.B.",
    ]


def test_participant_names_from_outlook_attendees_adds_missing_fio() -> None:
    from types import SimpleNamespace
    from unittest.mock import patch

    from app.services.meeting_attendees import participant_names_from_outlook_attendees

    entry = SimpleNamespace(
        outlook_item_id="occ-1",
        outlook_changekey="ck-1",
        payload={
            "attendees": [
                "director@turbo-don.ru",
                "extra@turbo-don.ru",
            ],
        },
    )
    with patch(
        "app.services.meeting_attendees.load_registry_outlook_attendee_entries",
        return_value=[
            ("Директор по развитию", "director@turbo-don.ru"),
            ("Кондratyuk M.B.", "extra@turbo-don.ru"),
        ],
    ):
        repaired = participant_names_from_outlook_attendees(
            entry,
            seed_names=["Директор по развитию"],
        )

    assert repaired == ["Директор по развитию", "Кондratyuk M.B."]


def test_registry_item_read_marks_scheduled_series_card() -> None:
    from app.services.meeting_mappers import registry_item_read

    series_id = uuid.uuid4()
    item = registry_item_read(
        SimpleNamespace(
            memo_ref_key=str(uuid.uuid4()),
            memo_number=None,
            title="Технический совет",
            subject="Технический совет",
            location=None,
            initiator_name=None,
            manager_name=None,
            participants_count=1,
            slot_start=datetime(2026, 7, 16, 6, 0, tzinfo=timezone.utc),
            slot_end=datetime(2026, 7, 16, 7, 0, tzinfo=timezone.utc),
            stage=MeetingRegistryStage.SCHEDULED,
            invitations_sent_at=datetime(2026, 7, 15, 8, 0, tzinfo=timezone.utc),
            approved_at=None,
            protocol_number=None,
            outlook_item_id="occ-1",
            outlook_changekey="ck-1",
            outlook_meeting_url="https://example.test/meeting",
            cancelled_at=None,
            updated_at=datetime(2026, 7, 15, 8, 0, tzinfo=timezone.utc),
            payload={
                "source": "scheduled_series",
                "series_recurrence_label": "ежедневно, 9:00",
            },
            scheduled_meeting_id=series_id,
        )
    )

    assert item.is_scheduled_series is True
    assert item.scheduled_meeting_id == str(series_id)
    assert item.scheduled_series_badge == "Серия"
    assert item.scheduled_series_recurrence_label == "ежедневно, 9:00"
