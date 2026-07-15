from __future__ import annotations

import uuid
from datetime import date, time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.enums import (
    ScheduledMeetingFrequency,
    ScheduledMeetingStatus,
    ScheduledMeetingType,
    ScheduledMeetingWeekday,
)
from app.schemas.scheduled_meeting import ScheduledMeetingCreate, ScheduledMeetingRecurrencePayload
from app.services.scheduled_meeting_service import (
    ScheduledMeetingService,
    ScheduledMeetingServiceError,
)


def _meeting_stub(
    *,
    meeting_id: uuid.UUID,
    title: str,
    department_id: uuid.UUID,
    department_name: str = "Главный инженер",
) -> SimpleNamespace:
    department = SimpleNamespace(id=department_id, name=department_name, is_active=True)
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
                department_id=department_id,
                department=department,
                sort_order=0,
                is_required=True,
            )
        ],
    )


@pytest.mark.asyncio
async def test_list_scheduled_meetings_returns_all_series() -> None:
    db = AsyncMock()
    department_id = uuid.uuid4()
    first_id = uuid.uuid4()
    second_id = uuid.uuid4()

    meetings = [
        _meeting_stub(meeting_id=first_id, title="Альфа", department_id=department_id),
        _meeting_stub(meeting_id=second_id, title="Бета", department_id=department_id),
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
    assert result[0].participants[0].department_name == "Главный инженер"


@pytest.mark.asyncio
async def test_create_scheduled_meeting_persists_participants() -> None:
    db = AsyncMock()
    department_id = uuid.uuid4()
    meeting_id = uuid.uuid4()

    meeting = _meeting_stub(
        meeting_id=meeting_id,
        title="Технический совет",
        department_id=department_id,
    )

    department_lookup = MagicMock()
    department_lookup.scalars.return_value.all.return_value = [
        SimpleNamespace(
            id=department_id,
            name="Главный инженер",
            is_active=True,
        )
    ]
    loaded_result = MagicMock()
    loaded_result.scalar_one_or_none.return_value = meeting

    db.execute = AsyncMock(side_effect=[department_lookup, loaded_result])

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
        participants=[{"department_id": department_id}],
    )

    result = await ScheduledMeetingService(db).create(payload)

    assert result.title == "Технический совет"
    assert result.recurrence_label == "еженедельно, вторник 10:00"
    assert result.series_end_date == date(2026, 12, 31)
    assert len(result.participants) == 1
    assert result.participants[0].department_name == "Главный инженер"
    assert result.payload == {"comment": "Дополнительная информация"}
    assert db.add.call_count == 2


@pytest.mark.asyncio
async def test_create_scheduled_meeting_accepts_inactive_position_department() -> None:
    db = AsyncMock()
    department_id = uuid.uuid4()
    meeting_id = uuid.uuid4()

    meeting = _meeting_stub(
        meeting_id=meeting_id,
        title="Технический совет",
        department_id=department_id,
        department_name="ФИНАНСОВЫЙ ДИРЕКТОР",
    )

    department_lookup = MagicMock()
    department_lookup.scalars.return_value.all.return_value = [
        SimpleNamespace(
            id=department_id,
            name="ФИНАНСОВЫЙ ДИРЕКТОР",
            is_active=False,
        )
    ]
    loaded_result = MagicMock()
    loaded_result.scalar_one_or_none.return_value = meeting

    db.execute = AsyncMock(side_effect=[department_lookup, loaded_result])

    def _assign_id(obj) -> None:
        if getattr(obj, "id", None) is None:
            obj.id = meeting_id

    db.add = MagicMock(side_effect=lambda obj: _assign_id(obj))
    db.flush = AsyncMock()

    payload = ScheduledMeetingCreate(
        title="Технический совет",
        meeting_type=ScheduledMeetingType.PLANNED,
        recurrence=ScheduledMeetingRecurrencePayload(
            frequency=ScheduledMeetingFrequency.WEEKLY,
            interval=1,
            weekday=ScheduledMeetingWeekday.TUESDAY,
            time_local=time(10, 0),
        ),
        participants=[{"department_id": department_id}],
    )

    result = await ScheduledMeetingService(db).create(payload)

    assert result.participants[0].department_name == "ФИНАНСОВЫЙ ДИРЕКТОР"


@pytest.mark.asyncio
async def test_create_scheduled_meeting_rejects_unknown_department() -> None:
    db = AsyncMock()
    department_id = uuid.uuid4()

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
        participants=[{"department_id": department_id}],
    )

    with pytest.raises(ScheduledMeetingServiceError, match="Не найдены активные"):
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
    department_id = uuid.uuid4()
    meeting = _meeting_stub(
        meeting_id=meeting_id,
        title="Технический совет",
        department_id=department_id,
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
