from __future__ import annotations

import uuid
from datetime import date, datetime, time
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
        outlook_series_id="master-1",
        outlook_changekey="ck-master",
        outlook_meeting_url="https://example.test/meeting",
        participants=[
            SimpleNamespace(
                sort_order=0,
                department=department,
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
