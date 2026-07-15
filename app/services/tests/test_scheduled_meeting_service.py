from __future__ import annotations

import uuid
from datetime import date, datetime, time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from app.models.enums import (
    ScheduledMeetingFrequency,
    ScheduledMeetingStatus,
    ScheduledMeetingType,
    ScheduledMeetingWeekday,
)
from app.schemas.scheduled_meeting import ScheduledMeetingCreate, ScheduledMeetingRecurrencePayload
from app.services.scheduled_meeting_occurrences import SeriesOccurrence
from app.services.scheduled_meeting_service import (
    ScheduledMeetingService,
    ScheduledMeetingServiceError,
)


def _meeting_stub(
    *,
    meeting_id: uuid.UUID,
    title: str,
    position_id: uuid.UUID,
    position_name: str = "Главный инженер",
) -> SimpleNamespace:
    position = SimpleNamespace(id=position_id, name=position_name, is_active=True)
    return SimpleNamespace(
        id=meeting_id,
        title=title,
        meeting_type=ScheduledMeetingType.PLANNED,
        status=ScheduledMeetingStatus.CREATED,
        time_local=time(10, 0),
        duration_minutes=60,
        frequency=ScheduledMeetingFrequency.WEEKLY,
        interval=1,
        monthly_mode=None,
        day_of_month=None,
        weekday=ScheduledMeetingWeekday.TUESDAY,
        weekday_position=None,
        series_start_date=date(2026, 1, 1),
        series_end_date=date(2026, 12, 31),
        recurrence_label="еженедельно, вторник 10:00",
        recurrence_rule={"frequency": "weekly"},
        outlook_series_id=None,
        outlook_changekey=None,
        outlook_meeting_url=None,
        payload={"comment": "Дополнительная информация"},
        participants=[
            SimpleNamespace(
                id=uuid.uuid4(),
                position_id=position_id,
                position=position,
                sort_order=0,
                is_required=True,
            )
        ],
    )


@pytest.mark.asyncio
async def test_list_scheduled_meetings_returns_all_series() -> None:
    db = AsyncMock()
    position_id = uuid.uuid4()
    first_id = uuid.uuid4()
    second_id = uuid.uuid4()

    meetings = [
        _meeting_stub(meeting_id=first_id, title="Альфа", position_id=position_id),
        _meeting_stub(meeting_id=second_id, title="Бета", position_id=position_id),
    ]

    scalars = MagicMock()
    scalars.all.return_value = meetings
    execute_result = MagicMock()
    execute_result.scalars.return_value = scalars
    db.execute = AsyncMock(return_value=execute_result)

    result = await ScheduledMeetingService(db).list()

    assert len(result) == 2
    assert result[0].title == "Альфа"
    assert result[1].title == "Бета"
    assert result[0].participants[0].position_name == "Главный инженер"


@pytest.mark.asyncio
async def test_get_scheduled_meeting_returns_series_from_db() -> None:
    db = AsyncMock()
    position_id = uuid.uuid4()
    meeting_id = uuid.uuid4()
    meeting = _meeting_stub(meeting_id=meeting_id, title="Техсовет", position_id=position_id)

    scalars = MagicMock()
    execute_result = MagicMock()
    execute_result.scalar_one_or_none.return_value = meeting
    db.execute = AsyncMock(return_value=execute_result)

    result = await ScheduledMeetingService(db).get(meeting_id)

    assert result.id == meeting_id
    assert result.title == "Техсовет"
    assert result.participants[0].position_id == position_id


@pytest.mark.asyncio
async def test_get_scheduled_meeting_raises_404_when_missing() -> None:
    db = AsyncMock()

    execute_result = MagicMock()
    execute_result.scalar_one_or_none.return_value = None
    db.execute = AsyncMock(return_value=execute_result)

    with pytest.raises(ScheduledMeetingServiceError) as exc:
        await ScheduledMeetingService(db).get(uuid.uuid4())

    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_create_scheduled_meeting_persists_participants() -> None:
    db = AsyncMock()
    position_id = uuid.uuid4()
    meeting_id = uuid.uuid4()

    meeting = _meeting_stub(
        meeting_id=meeting_id,
        title="Технический совет",
        position_id=position_id,
    )

    position_lookup = MagicMock()
    position_lookup.scalars.return_value.all.return_value = [
        SimpleNamespace(
            id=position_id,
            name="Главный инженер",
            is_active=True,
        )
    ]
    loaded_result = MagicMock()
    loaded_result.scalar_one_or_none.return_value = meeting

    db.execute = AsyncMock(side_effect=[position_lookup, loaded_result])

    def _assign_id(obj) -> None:
        if getattr(obj, "id", None) is None:
            obj.id = meeting_id

    db.add = MagicMock(side_effect=lambda obj: _assign_id(obj))
    db.flush = AsyncMock()

    payload = ScheduledMeetingCreate(
        title="Технический совет",
        meeting_type=ScheduledMeetingType.PLANNED,
        status=ScheduledMeetingStatus.CREATED,
        series_start_date=date(2026, 1, 1),
        series_end_date=date(2026, 12, 31),
        comment="Дополнительная информация",
        recurrence=ScheduledMeetingRecurrencePayload(
            frequency=ScheduledMeetingFrequency.WEEKLY,
            interval=1,
            weekday=ScheduledMeetingWeekday.TUESDAY,
            time_local=time(10, 0),
        ),
        participants=[{"position_id": position_id}],
    )

    result = await ScheduledMeetingService(db).create(payload)

    assert result.title == "Технический совет"
    assert result.recurrence_label == "еженедельно, вторник 10:00"
    assert result.series_end_date == date(2026, 12, 31)
    assert len(result.participants) == 1
    assert result.participants[0].position_name == "Главный инженер"
    assert result.payload == {"comment": "Дополнительная информация"}
    assert db.add.call_count == 2


@pytest.mark.asyncio
async def test_create_scheduled_meeting_rejects_inactive_position() -> None:
    db = AsyncMock()
    position_id = uuid.uuid4()

    execute_result = MagicMock()
    execute_result.scalars.return_value.all.return_value = [
        SimpleNamespace(id=position_id, name="ФИНАНСОВЫЙ ДИРЕКТОР", is_active=False)
    ]
    db.execute = AsyncMock(return_value=execute_result)

    payload = ScheduledMeetingCreate(
        title="Технический совет",
        meeting_type=ScheduledMeetingType.PLANNED,
        recurrence=ScheduledMeetingRecurrencePayload(
            frequency=ScheduledMeetingFrequency.WEEKLY,
            interval=1,
            weekday=ScheduledMeetingWeekday.TUESDAY,
            time_local=time(10, 0),
        ),
        participants=[{"position_id": position_id}],
    )

    with pytest.raises(ScheduledMeetingServiceError, match="Не найдены активные должности"):
        await ScheduledMeetingService(db).create(payload)


@pytest.mark.asyncio
async def test_create_scheduled_meeting_rejects_unknown_position() -> None:
    db = AsyncMock()
    position_id = uuid.uuid4()

    execute_result = MagicMock()
    execute_result.scalars.return_value.all.return_value = []
    db.execute = AsyncMock(return_value=execute_result)

    payload = ScheduledMeetingCreate(
        title="Технический совет",
        meeting_type=ScheduledMeetingType.PLANNED,
        recurrence=ScheduledMeetingRecurrencePayload(
            frequency=ScheduledMeetingFrequency.WEEKLY,
            interval=1,
            weekday=ScheduledMeetingWeekday.TUESDAY,
            time_local=time(10, 0),
        ),
        participants=[{"position_id": position_id}],
    )

    with pytest.raises(ScheduledMeetingServiceError, match="Не найдены активные должности"):
        await ScheduledMeetingService(db).create(payload)


@pytest.mark.asyncio
async def test_archive_expired_series_returns_archived_ids() -> None:
    db = AsyncMock()
    archived_id = uuid.uuid4()

    execute_result = MagicMock()
    execute_result.scalars.return_value.all.return_value = [archived_id]
    db.execute = AsyncMock(return_value=execute_result)

    result = await ScheduledMeetingService(db).archive_expired_series(as_of_date=date(2026, 7, 18))

    assert result["archived_count"] == 1
    assert result["archived_ids"] == [str(archived_id)]
    assert result["as_of_date"] == "2026-07-18"


@pytest.mark.asyncio
async def test_archive_expired_series_returns_zero_when_none_expired() -> None:
    db = AsyncMock()

    execute_result = MagicMock()
    execute_result.scalars.return_value.all.return_value = []
    db.execute = AsyncMock(return_value=execute_result)

    result = await ScheduledMeetingService(db).archive_expired_series(as_of_date=date(2026, 7, 17))

    assert result["archived_count"] == 0
    assert result["archived_ids"] == []
    assert result["as_of_date"] == "2026-07-17"


@pytest.mark.asyncio
async def test_plan_scheduled_meeting_triggers_registry_sync() -> None:
    db = AsyncMock()
    meeting_id = uuid.uuid4()
    position_id = uuid.uuid4()
    meeting = _meeting_stub(
        meeting_id=meeting_id,
        title="Технический совет",
        position_id=position_id,
    )
    meeting.status = ScheduledMeetingStatus.CREATED
    meeting.outlook_series_id = "master-1"

    loaded_result = MagicMock()
    loaded_result.scalar_one_or_none.return_value = meeting
    db.execute = AsyncMock(return_value=loaded_result)

    with (
        patch(
            "app.services.scheduled_meeting_service.plan_scheduled_meeting_in_outlook",
            AsyncMock(),
        ) as plan_outlook,
        patch(
            "app.services.scheduled_meeting_registry_sync.ScheduledMeetingRegistrySyncService.sync_series_card",
            AsyncMock(),
        ) as sync_card,
    ):
        result = await ScheduledMeetingService(db).plan(meeting_id)

    plan_outlook.assert_awaited_once()
    sync_card.assert_awaited_once_with(meeting_id)
    assert result.id == meeting_id


@pytest.mark.asyncio
async def test_get_detail_reads_next_occurrence_from_outlook() -> None:
    db = AsyncMock()
    meeting_id = uuid.uuid4()
    position_id = uuid.uuid4()
    meeting = _meeting_stub(
        meeting_id=meeting_id,
        title="Технический совет",
        position_id=position_id,
    )
    meeting.status = ScheduledMeetingStatus.PLANNED
    meeting.outlook_series_id = "master-1"
    meeting.outlook_meeting_url = "https://outlook.example/series"

    loaded_result = MagicMock()
    loaded_result.scalar_one_or_none.return_value = meeting
    db.execute = AsyncMock(return_value=loaded_result)

    tz = ZoneInfo("Europe/Moscow")
    next_occurrence = SeriesOccurrence(
        occurrence_date=date(2026, 7, 16),
        slot_start=datetime(2026, 7, 16, 9, 0, tzinfo=tz),
        slot_end=datetime(2026, 7, 16, 9, 30, tzinfo=tz),
        outlook_item_id="occ-2",
        outlook_changekey="ck-2",
        subject="Технический совет",
        is_cancelled=False,
        source="outlook",
    )
    past_occurrence = SeriesOccurrence(
        occurrence_date=date(2026, 7, 15),
        slot_start=datetime(2026, 7, 15, 9, 0, tzinfo=tz),
        slot_end=datetime(2026, 7, 15, 9, 30, tzinfo=tz),
        outlook_item_id="occ-1",
        outlook_changekey="ck-1",
        subject="Технический совет",
        is_cancelled=False,
        source="outlook",
    )

    with (
        patch(
            "app.services.scheduled_meeting_service.asyncio.to_thread",
            AsyncMock(return_value=([past_occurrence, next_occurrence], "outlook")),
        ),
        patch(
            "app.services.scheduled_meeting_registry_sync.ScheduledMeetingRegistrySyncService.sync_series_card",
            AsyncMock(),
        ) as sync_card,
        patch(
            "app.services.meeting_registry_service.MeetingRegistryService.get_entry_by_scheduled_meeting_id",
            AsyncMock(return_value=None),
        ),
    ):
        detail = await ScheduledMeetingService(db).get_detail(meeting_id)

    sync_card.assert_awaited_once_with(meeting_id)

    assert detail.next_occurrence is not None
    assert detail.next_occurrence.occurrence_date == date(2026, 7, 16)
    assert detail.next_occurrence.source == "outlook"
    assert len(detail.past_occurrences) == 1
    assert detail.past_occurrences[0].occurrence_date == date(2026, 7, 15)
