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
from app.schemas.scheduled_meeting import (
    ScheduledMeetingCancelRequest,
    ScheduledMeetingCreate,
    ScheduledMeetingParticipantCreate,
    ScheduledMeetingRecurrencePayload,
)
from app.services.scheduled_meeting_occurrences import SeriesOccurrence
from app.services.scheduled_meeting_person import ResolvedPerson
from app.services.scheduled_meeting_service import (
    ScheduledMeetingService,
    ScheduledMeetingServiceError,
)


def _resolved_person(
    user_id: uuid.UUID,
    *,
    position_id: uuid.UUID | None = None,
    fio: str = "Главный инженер",
    email: str = "engineer@turbo-don.ru",
) -> ResolvedPerson:
    return ResolvedPerson(
        user_id=user_id,
        fio=fio,
        email=email,
        position_id=position_id,
    )


def _meeting_stub(
    *,
    meeting_id: uuid.UUID,
    title: str,
    user_id: uuid.UUID | None = None,
    position_id: uuid.UUID | None = None,
    position_name: str = "Главный инженер",
    category_id: uuid.UUID | None = None,
    manager_user_id: uuid.UUID | None = None,
    responsible_user_id: uuid.UUID | None = None,
    manager_position_id: uuid.UUID | None = None,
    responsible_position_id: uuid.UUID | None = None,
) -> SimpleNamespace:
    category_id = category_id or uuid.uuid4()
    position_id = position_id or uuid.uuid4()
    user_id = user_id or uuid.uuid4()
    manager_user_id = manager_user_id or uuid.uuid4()
    responsible_user_id = responsible_user_id or uuid.uuid4()
    manager_position_id = manager_position_id or position_id
    responsible_position_id = responsible_position_id or position_id
    position = SimpleNamespace(id=position_id, name=position_name, is_active=True)
    manager_position = SimpleNamespace(id=manager_position_id, name=position_name, is_active=True)
    responsible_position = SimpleNamespace(
        id=responsible_position_id,
        name=position_name,
        is_active=True,
    )
    manager_user = SimpleNamespace(
        id=manager_user_id,
        full_name="Директор",
        email="director@turbo-don.ru",
    )
    responsible_user = SimpleNamespace(
        id=responsible_user_id,
        full_name="Секретарь",
        email="secretary@turbo-don.ru",
    )
    participant_user = SimpleNamespace(
        id=user_id,
        full_name=position_name,
        email="engineer@turbo-don.ru",
    )
    category = SimpleNamespace(id=category_id, name="Комитет", sort_order=1, is_active=True)
    return SimpleNamespace(
        id=meeting_id,
        title=title,
        meeting_category_id=category_id,
        meeting_category=category,
        manager_user_id=manager_user_id,
        manager_user=manager_user,
        responsible_user_id=responsible_user_id,
        responsible_user=responsible_user,
        manager_position_id=manager_position_id,
        manager_position=manager_position,
        responsible_position_id=responsible_position_id,
        responsible_position=responsible_position,
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
                user_id=manager_user_id,
                person_fio="Директор",
                person_email="director@turbo-don.ru",
                position_id=manager_position_id,
                position=manager_position,
                user=manager_user,
                sort_order=0,
                is_required=True,
            ),
            SimpleNamespace(
                id=uuid.uuid4(),
                user_id=responsible_user_id,
                person_fio="Секретарь",
                person_email="secretary@turbo-don.ru",
                position_id=responsible_position_id,
                position=responsible_position,
                user=responsible_user,
                sort_order=1,
                is_required=True,
            ),
            SimpleNamespace(
                id=uuid.uuid4(),
                user_id=user_id,
                person_fio=position_name,
                person_email="engineer@turbo-don.ru",
                position_id=position_id,
                position=position,
                user=participant_user,
                sort_order=2,
                is_required=True,
            ),
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
    assert result.participants[0].user_id is not None


@pytest.mark.asyncio
async def test_get_scheduled_meeting_raises_404_when_missing() -> None:
    db = AsyncMock()

    execute_result = MagicMock()
    execute_result.scalar_one_or_none.return_value = None
    db.execute = AsyncMock(return_value=execute_result)

    with pytest.raises(ScheduledMeetingServiceError) as exc:
        await ScheduledMeetingService(db).get(uuid.uuid4())

    assert exc.value.status_code == 404


def _participants_payload(
    meeting: SimpleNamespace,
    *,
    extra: list[ScheduledMeetingParticipantCreate] | None = None,
    exclude_user_ids: set[uuid.UUID] | None = None,
) -> list[ScheduledMeetingParticipantCreate]:
    excluded = exclude_user_ids or set()
    items = [
        ScheduledMeetingParticipantCreate(
            user_id=participant.user_id,
            person_fio=participant.person_fio,
            person_email=participant.person_email,
            position_id=participant.position_id,
            sort_order=participant.sort_order,
        )
        for participant in sorted(meeting.participants, key=lambda item: item.sort_order)
        if participant.user_id not in excluded
    ]
    if extra:
        items.extend(extra)
    return items


@pytest.mark.asyncio
async def test_create_scheduled_meeting_persists_participants() -> None:
    db = AsyncMock()
    position_id = uuid.uuid4()
    manager_user_id = uuid.uuid4()
    responsible_user_id = uuid.uuid4()
    participant_user_id = uuid.uuid4()
    manager_position_id = uuid.uuid4()
    responsible_position_id = uuid.uuid4()
    category_id = uuid.uuid4()
    meeting_id = uuid.uuid4()

    meeting = _meeting_stub(
        meeting_id=meeting_id,
        title="Технический совет",
        user_id=participant_user_id,
        position_id=position_id,
        category_id=category_id,
        manager_user_id=manager_user_id,
        responsible_user_id=responsible_user_id,
        manager_position_id=manager_position_id,
        responsible_position_id=responsible_position_id,
    )

    position_lookup = MagicMock()
    position_lookup.scalars.return_value.all.return_value = [
        SimpleNamespace(id=position_id, name="Главный инженер", is_active=True),
        SimpleNamespace(id=manager_position_id, name="Директор", is_active=True),
        SimpleNamespace(id=responsible_position_id, name="Секретарь", is_active=True),
    ]
    loaded_result = MagicMock()
    loaded_result.scalar_one_or_none.return_value = meeting

    db.get = AsyncMock(return_value=SimpleNamespace(id=category_id, is_active=True))
    db.execute = AsyncMock(return_value=loaded_result)

    def _assign_id(obj) -> None:
        if getattr(obj, "id", None) is None:
            obj.id = meeting_id

    db.add = MagicMock(side_effect=lambda obj: _assign_id(obj))
    db.flush = AsyncMock()

    payload = ScheduledMeetingCreate(
        title="Технический совет",
        meeting_category_id=category_id,
        manager_user_id=manager_user_id,
        responsible_user_id=responsible_user_id,
        meeting_type=ScheduledMeetingType.PLANNED,
        status=ScheduledMeetingStatus.CREATED,
        series_start_date=date(2026, 1, 1),
        series_end_date=date(2026, 12, 31),
        comment="Дополнительная информация",
        recurrence_label="еженедельно, вторник 10:00",
        recurrence=ScheduledMeetingRecurrencePayload(
            frequency=ScheduledMeetingFrequency.WEEKLY,
            interval=1,
            weekday=ScheduledMeetingWeekday.TUESDAY,
            time_local=time(10, 0),
        ),
        participants=[
            {
                "user_id": participant_user_id,
                "person_fio": "Главный инженер",
                "person_email": "engineer@turbo-don.ru",
                "position_id": position_id,
            }
        ],
    )

    with patch.object(
        ScheduledMeetingService,
        "_resolve_role_person",
        AsyncMock(
            side_effect=[
                _resolved_person(
                    manager_user_id,
                    position_id=manager_position_id,
                    fio="Директор",
                    email="director@turbo-don.ru",
                ),
                _resolved_person(
                    responsible_user_id,
                    position_id=responsible_position_id,
                    fio="Секретарь",
                    email="secretary@turbo-don.ru",
                ),
            ]
        ),
    ), patch.object(
        ScheduledMeetingService,
        "_resolve_participant_create",
        AsyncMock(
            return_value=ScheduledMeetingParticipantCreate(
                user_id=participant_user_id,
                person_fio="Главный инженер",
                person_email="engineer@turbo-don.ru",
                position_id=position_id,
            )
        ),
    ), patch.object(
        ScheduledMeetingService,
        "_ensure_positions_exist",
        AsyncMock(),
    ):
        result = await ScheduledMeetingService(db).create(payload)

    assert result.title == "Технический совет"
    assert result.meeting_category_id == category_id
    assert result.manager_user_id == manager_user_id
    assert result.responsible_user_id == responsible_user_id
    assert result.recurrence_label == "еженедельно, вторник 10:00"
    assert result.series_end_date == date(2026, 12, 31)
    assert len(result.participants) == 3
    participant_names = {item.person_fio for item in result.participants}
    assert "Главный инженер" in participant_names
    assert "Директор" in participant_names
    assert result.payload == {"comment": "Дополнительная информация"}
    assert db.add.call_count == 4


@pytest.mark.asyncio
async def test_create_scheduled_meeting_rejects_inactive_position() -> None:
    db = AsyncMock()
    position_id = uuid.uuid4()
    user_id = uuid.uuid4()

    execute_result = MagicMock()
    execute_result.scalars.return_value.all.return_value = [
        SimpleNamespace(id=position_id, name="ФИНАНСОВЫЙ ДИРЕКТОР", is_active=False)
    ]
    db.get = AsyncMock(return_value=SimpleNamespace(id=uuid.uuid4(), is_active=True))
    db.execute = AsyncMock(return_value=execute_result)

    payload = ScheduledMeetingCreate(
        title="Технический совет",
        meeting_category_id=uuid.uuid4(),
        manager_user_id=user_id,
        responsible_user_id=user_id,
        meeting_type=ScheduledMeetingType.PLANNED,
        recurrence=ScheduledMeetingRecurrencePayload(
            frequency=ScheduledMeetingFrequency.WEEKLY,
            interval=1,
            weekday=ScheduledMeetingWeekday.TUESDAY,
            time_local=time(10, 0),
        ),
        participants=[{"user_id": user_id, "position_id": position_id}],
    )

    with patch.object(
        ScheduledMeetingService,
        "_resolve_role_person",
        AsyncMock(return_value=_resolved_person(user_id, position_id=position_id)),
    ), patch.object(
        ScheduledMeetingService,
        "_resolve_participant_create",
        AsyncMock(
            return_value=ScheduledMeetingParticipantCreate(
                user_id=user_id,
                position_id=position_id,
            )
        ),
    ):
        with pytest.raises(ScheduledMeetingServiceError, match="Не найдены активные должности"):
            await ScheduledMeetingService(db).create(payload)


@pytest.mark.asyncio
async def test_create_scheduled_meeting_rejects_unknown_position() -> None:
    db = AsyncMock()
    position_id = uuid.uuid4()
    user_id = uuid.uuid4()

    execute_result = MagicMock()
    execute_result.scalars.return_value.all.return_value = []
    db.get = AsyncMock(return_value=SimpleNamespace(id=uuid.uuid4(), is_active=True))
    db.execute = AsyncMock(return_value=execute_result)

    payload = ScheduledMeetingCreate(
        title="Технический совет",
        meeting_category_id=uuid.uuid4(),
        manager_user_id=user_id,
        responsible_user_id=user_id,
        meeting_type=ScheduledMeetingType.PLANNED,
        recurrence=ScheduledMeetingRecurrencePayload(
            frequency=ScheduledMeetingFrequency.WEEKLY,
            interval=1,
            weekday=ScheduledMeetingWeekday.TUESDAY,
            time_local=time(10, 0),
        ),
        participants=[{"user_id": user_id, "position_id": position_id}],
    )

    with patch.object(
        ScheduledMeetingService,
        "_resolve_role_person",
        AsyncMock(return_value=_resolved_person(user_id, position_id=position_id)),
    ), patch.object(
        ScheduledMeetingService,
        "_resolve_participant_create",
        AsyncMock(
            return_value=ScheduledMeetingParticipantCreate(
                user_id=user_id,
                position_id=position_id,
            )
        ),
    ):
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
async def test_cancel_scheduled_series_archives_and_cancels_outlook() -> None:
    db = AsyncMock()
    meeting_id = uuid.uuid4()
    position_id = uuid.uuid4()
    meeting = _meeting_stub(
        meeting_id=meeting_id,
        title="Периодическое совещание",
        position_id=position_id,
    )
    meeting.status = ScheduledMeetingStatus.PLANNED
    meeting.outlook_series_id = "master-1"
    meeting.outlook_changekey = "ck-1"

    loaded_result = MagicMock()
    loaded_result.scalar_one_or_none.return_value = meeting
    db.execute = AsyncMock(return_value=loaded_result)
    db.flush = AsyncMock()
    current_user = SimpleNamespace(id=uuid.uuid4())

    with (
        patch(
            "app.services.scheduled_meeting_service.cancel_scheduled_meeting_in_outlook",
            AsyncMock(return_value={"status": "cancelled"}),
        ),
        patch(
            "app.services.meeting_registry_service.MeetingRegistryService.get_entry_by_scheduled_meeting_id",
            AsyncMock(return_value=None),
        ),
    ):
        result = await ScheduledMeetingService(db).cancel(
            meeting_id,
            ScheduledMeetingCancelRequest(),
            current_user=current_user,
        )

    assert result.cancelled is True
    assert result.outlook_cancelled is True
    assert meeting.status == ScheduledMeetingStatus.ARCHIVE


@pytest.mark.asyncio
async def test_cancel_scheduled_series_without_outlook_id_archives_with_warning() -> None:
    db = AsyncMock()
    meeting_id = uuid.uuid4()
    position_id = uuid.uuid4()
    meeting = _meeting_stub(
        meeting_id=meeting_id,
        title="Периодическое совещание",
        position_id=position_id,
    )
    meeting.status = ScheduledMeetingStatus.PLANNED
    meeting.outlook_series_id = None

    loaded_result = MagicMock()
    loaded_result.scalar_one_or_none.return_value = meeting
    db.execute = AsyncMock(return_value=loaded_result)
    db.flush = AsyncMock()
    current_user = SimpleNamespace(id=uuid.uuid4())

    with patch(
        "app.services.meeting_registry_service.MeetingRegistryService.get_entry_by_scheduled_meeting_id",
        AsyncMock(return_value=None),
    ):
        result = await ScheduledMeetingService(db).cancel(
            meeting_id,
            ScheduledMeetingCancelRequest(),
            current_user=current_user,
        )

    assert result.cancelled is True
    assert result.outlook_cancelled is False
    assert result.outlook_warning is not None
    assert meeting.status == ScheduledMeetingStatus.ARCHIVE


@pytest.mark.asyncio
async def test_cancel_scheduled_series_rejects_created_status() -> None:
    db = AsyncMock()
    meeting_id = uuid.uuid4()
    position_id = uuid.uuid4()
    meeting = _meeting_stub(
        meeting_id=meeting_id,
        title="Периодическое совещание",
        position_id=position_id,
    )
    meeting.status = ScheduledMeetingStatus.CREATED

    loaded_result = MagicMock()
    loaded_result.scalar_one_or_none.return_value = meeting
    db.execute = AsyncMock(return_value=loaded_result)
    current_user = SimpleNamespace(id=uuid.uuid4())

    with pytest.raises(ScheduledMeetingServiceError, match="распланированную"):
        await ScheduledMeetingService(db).cancel(
            meeting_id,
            ScheduledMeetingCancelRequest(),
            current_user=current_user,
        )


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
        occurrence_date=date(2027, 7, 16),
        slot_start=datetime(2027, 7, 16, 9, 0, tzinfo=tz),
        slot_end=datetime(2027, 7, 16, 9, 30, tzinfo=tz),
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
    assert detail.next_occurrence.occurrence_date == date(2027, 7, 16)
    assert detail.next_occurrence.source == "outlook"
    assert len(detail.upcoming_occurrences) == 1
    assert detail.upcoming_occurrences[0].occurrence_date == date(2027, 7, 16)
    assert len(detail.past_occurrences) == 1
    assert detail.past_occurrences[0].occurrence_date == date(2026, 7, 15)


@pytest.mark.asyncio
async def test_update_series_end_shortens_db_for_created_series() -> None:
    db = AsyncMock()
    meeting_id = uuid.uuid4()
    position_id = uuid.uuid4()
    meeting = _meeting_stub(meeting_id=meeting_id, title="Серия", position_id=position_id)
    meeting.status = ScheduledMeetingStatus.CREATED
    meeting.series_start_date = date(2026, 7, 15)
    meeting.series_end_date = date(2026, 7, 17)

    execute_result = MagicMock()
    execute_result.scalar_one_or_none.return_value = meeting
    db.execute = AsyncMock(return_value=execute_result)
    db.flush = AsyncMock()

    from app.schemas.scheduled_meeting import ScheduledMeetingUpdate

    result = await ScheduledMeetingService(db).update(
        meeting_id,
        ScheduledMeetingUpdate(series_end_date=date(2026, 7, 16)),
    )

    assert result.applied_changes.db_updated is True
    assert result.applied_changes.outlook_updated is False
    assert result.applied_changes.changes == ["series_end_date"]
    assert meeting.series_end_date == date(2026, 7, 16)


@pytest.mark.asyncio
async def test_update_series_end_calls_outlook_for_planned_series() -> None:
    db = AsyncMock()
    meeting_id = uuid.uuid4()
    position_id = uuid.uuid4()
    meeting = _meeting_stub(meeting_id=meeting_id, title="Серия", position_id=position_id)
    meeting.status = ScheduledMeetingStatus.PLANNED
    meeting.series_start_date = date(2026, 7, 15)
    meeting.series_end_date = date(2026, 7, 17)
    meeting.outlook_series_id = "series-1"
    meeting.outlook_changekey = "ck-1"

    execute_result = MagicMock()
    execute_result.scalar_one_or_none.return_value = meeting
    db.execute = AsyncMock(return_value=execute_result)
    db.flush = AsyncMock()

    from app.schemas.scheduled_meeting import ScheduledMeetingUpdate

    with (
        patch(
            "app.services.scheduled_meeting_service.update_series_end_date_in_outlook",
            AsyncMock(
                return_value={
                    "action": "series_end_shortened",
                    "outlook_changekey": "ck-2",
                    "outlook_meeting_url": "https://outlook.example/series",
                }
            ),
        ) as outlook_update,
        patch(
            "app.services.scheduled_meeting_registry_sync.ScheduledMeetingRegistrySyncService.sync_series_card",
            AsyncMock(),
        ) as sync_card,
    ):
        result = await ScheduledMeetingService(db).update(
            meeting_id,
            ScheduledMeetingUpdate(series_end_date=date(2026, 7, 16)),
        )

    outlook_update.assert_awaited_once()
    sync_card.assert_awaited_once_with(meeting_id)
    assert result.applied_changes.outlook_updated is True
    assert result.applied_changes.outlook_actions == ["series_end_shortened"]
    assert meeting.series_end_date == date(2026, 7, 16)
    assert meeting.outlook_changekey == "ck-2"


@pytest.mark.asyncio
async def test_update_recurrence_calls_outlook_for_planned_series() -> None:
    db = AsyncMock()
    meeting_id = uuid.uuid4()
    position_id = uuid.uuid4()
    meeting = _meeting_stub(meeting_id=meeting_id, title="Серия", position_id=position_id)
    meeting.status = ScheduledMeetingStatus.PLANNED
    meeting.series_start_date = date(2026, 7, 15)
    meeting.series_end_date = date(2026, 7, 17)
    meeting.outlook_series_id = "series-1"
    meeting.outlook_changekey = "ck-1"

    execute_result = MagicMock()
    execute_result.scalar_one_or_none.return_value = meeting
    db.execute = AsyncMock(return_value=execute_result)
    db.flush = AsyncMock()

    from app.schemas.scheduled_meeting import ScheduledMeetingUpdate

    with (
        patch(
            "app.services.scheduled_meeting_service.update_series_recurrence_in_outlook",
            AsyncMock(
                return_value={
                    "action": "series_recurrence_updated",
                    "outlook_changekey": "ck-3",
                    "outlook_meeting_url": "https://outlook.example/series",
                }
            ),
        ) as outlook_update,
        patch(
            "app.services.scheduled_meeting_registry_sync.ScheduledMeetingRegistrySyncService.sync_series_card",
            AsyncMock(),
        ) as sync_card,
    ):
        result = await ScheduledMeetingService(db).update(
            meeting_id,
            ScheduledMeetingUpdate(
                recurrence=ScheduledMeetingRecurrencePayload(
                    frequency=ScheduledMeetingFrequency.DAILY,
                    interval=1,
                    time_local=time(9, 0),
                    duration_minutes=60,
                ),
            ),
        )

    outlook_update.assert_awaited_once()
    sync_card.assert_awaited_once_with(meeting_id)
    assert result.applied_changes.outlook_updated is True
    assert result.applied_changes.outlook_actions == ["series_recurrence_updated"]
    assert meeting.frequency == ScheduledMeetingFrequency.DAILY
    assert meeting.time_local == time(9, 0)
    assert meeting.outlook_changekey == "ck-3"


@pytest.mark.asyncio
async def test_update_recurrence_updates_db_for_created_series() -> None:
    db = AsyncMock()
    meeting_id = uuid.uuid4()
    position_id = uuid.uuid4()
    meeting = _meeting_stub(meeting_id=meeting_id, title="Серия", position_id=position_id)
    meeting.status = ScheduledMeetingStatus.CREATED
    meeting.series_start_date = date(2026, 7, 15)
    meeting.series_end_date = date(2026, 7, 17)

    execute_result = MagicMock()
    execute_result.scalar_one_or_none.return_value = meeting
    db.execute = AsyncMock(return_value=execute_result)
    db.flush = AsyncMock()

    from app.schemas.scheduled_meeting import ScheduledMeetingUpdate

    result = await ScheduledMeetingService(db).update(
        meeting_id,
        ScheduledMeetingUpdate(
            recurrence=ScheduledMeetingRecurrencePayload(
                frequency=ScheduledMeetingFrequency.DAILY,
                interval=1,
                time_local=time(9, 0),
                duration_minutes=60,
            ),
        ),
    )

    assert result.applied_changes.db_updated is True
    assert result.applied_changes.outlook_updated is False
    assert result.applied_changes.changes == ["recurrence"]
    assert meeting.frequency == ScheduledMeetingFrequency.DAILY
    assert meeting.time_local == time(9, 0)


@pytest.mark.asyncio
async def test_update_removes_participant_for_created_series() -> None:
    db = AsyncMock()
    meeting_id = uuid.uuid4()
    position_id = uuid.uuid4()
    other_user_id = uuid.uuid4()
    meeting = _meeting_stub(meeting_id=meeting_id, title="Серия", position_id=position_id)
    other_position = SimpleNamespace(id=uuid.uuid4(), name="Другая должность", is_active=True)
    meeting.participants.append(
        SimpleNamespace(
            id=uuid.uuid4(),
            user_id=other_user_id,
            person_fio="Другая должность",
            person_email="other@turbo-don.ru",
            position_id=other_position.id,
            position=other_position,
            user=SimpleNamespace(full_name="Другая должность", email="other@turbo-don.ru"),
            sort_order=3,
            is_required=True,
        )
    )
    meeting.status = ScheduledMeetingStatus.CREATED

    execute_result = MagicMock()
    execute_result.scalar_one_or_none.return_value = meeting
    db.execute = AsyncMock(return_value=execute_result)
    db.flush = AsyncMock()
    db.delete = AsyncMock()

    from app.schemas.scheduled_meeting import ScheduledMeetingUpdate

    with (
        patch.object(
            ScheduledMeetingService,
            "_resolve_participant_create",
            AsyncMock(side_effect=lambda item: item),
        ),
        patch.object(
            ScheduledMeetingService,
            "_apply_participant_changes",
            AsyncMock(return_value=([], ["Другая должность"])),
        ) as apply_participants,
        patch(
            "app.services.scheduled_meeting_registry_sync.ScheduledMeetingRegistrySyncService.sync_series_card",
            AsyncMock(),
        ) as sync_card,
    ):
        result = await ScheduledMeetingService(db).update(
            meeting_id,
            ScheduledMeetingUpdate(
                participants=_participants_payload(
                    meeting,
                    exclude_user_ids={other_user_id},
                ),
            ),
        )

    apply_participants.assert_awaited_once()
    sync_card.assert_not_awaited()
    assert result.applied_changes.db_updated is True
    assert result.applied_changes.outlook_updated is False
    assert result.applied_changes.changes == ["participants"]
    assert result.applied_changes.participants_removed == ["Другая должность"]


@pytest.mark.asyncio
async def test_update_adds_participant_for_created_series() -> None:
    db = AsyncMock()
    meeting_id = uuid.uuid4()
    position_id = uuid.uuid4()
    new_user_id = uuid.uuid4()
    meeting = _meeting_stub(meeting_id=meeting_id, title="Серия", position_id=position_id)
    meeting.status = ScheduledMeetingStatus.CREATED
    new_user_id = uuid.uuid4()

    execute_result = MagicMock()
    execute_result.scalar_one_or_none.return_value = meeting
    db.execute = AsyncMock(return_value=execute_result)
    db.flush = AsyncMock()
    db.add = MagicMock()

    from app.schemas.scheduled_meeting import ScheduledMeetingUpdate

    with (
        patch.object(
            ScheduledMeetingService,
            "_resolve_participant_create",
            AsyncMock(side_effect=lambda item: item),
        ),
        patch.object(
            ScheduledMeetingService,
            "_apply_participant_changes",
            AsyncMock(return_value=(["Новый участник"], [])),
        ) as apply_participants,
        patch(
            "app.services.scheduled_meeting_registry_sync.ScheduledMeetingRegistrySyncService.sync_series_card",
            AsyncMock(),
        ) as sync_card,
    ):
        result = await ScheduledMeetingService(db).update(
            meeting_id,
            ScheduledMeetingUpdate(
                participants=_participants_payload(
                    meeting,
                    extra=[
                        ScheduledMeetingParticipantCreate(
                            user_id=new_user_id,
                            person_fio="Новый участник",
                            person_email="new.user@turbo-don.ru",
                            sort_order=3,
                        )
                    ],
                ),
            ),
        )

    apply_participants.assert_awaited_once()
    sync_card.assert_not_awaited()
    assert result.applied_changes.db_updated is True
    assert result.applied_changes.outlook_updated is False
    assert result.applied_changes.changes == ["participants"]
    assert result.applied_changes.participants_added == ["Новый участник"]


@pytest.mark.asyncio
async def test_update_adds_participant_calls_outlook_for_planned_series() -> None:
    db = AsyncMock()
    meeting_id = uuid.uuid4()
    position_id = uuid.uuid4()
    new_user_id = uuid.uuid4()
    meeting = _meeting_stub(meeting_id=meeting_id, title="Серия", position_id=position_id)
    meeting.status = ScheduledMeetingStatus.PLANNED
    meeting.outlook_series_id = "series-1"
    meeting.outlook_changekey = "ck-1"
    new_user_id = uuid.uuid4()

    execute_result = MagicMock()
    execute_result.scalar_one_or_none.return_value = meeting
    db.execute = AsyncMock(return_value=execute_result)
    db.flush = AsyncMock()

    from app.schemas.scheduled_meeting import ScheduledMeetingUpdate

    with (
        patch.object(
            ScheduledMeetingService,
            "_resolve_participant_create",
            AsyncMock(side_effect=lambda item: item),
        ),
        patch.object(
            ScheduledMeetingService,
            "_apply_participant_changes",
            AsyncMock(return_value=(["Новый участник"], [])),
        ),
        patch.object(
            ScheduledMeetingService,
            "_resolve_emails_for_user_ids",
            AsyncMock(return_value=["new.user@turbo-don.ru"]),
        ),
        patch(
            "app.services.scheduled_meeting_service.sync_series_participants_in_outlook",
            AsyncMock(return_value={"action": "participants_added", "outlook_changekey": "ck-2"}),
        ) as outlook_sync,
        patch(
            "app.services.scheduled_meeting_registry_sync.ScheduledMeetingRegistrySyncService.sync_series_card",
            AsyncMock(),
        ) as sync_card,
    ):
        result = await ScheduledMeetingService(db).update(
            meeting_id,
            ScheduledMeetingUpdate(
                participants=_participants_payload(
                    meeting,
                    extra=[
                        ScheduledMeetingParticipantCreate(
                            user_id=new_user_id,
                            person_fio="Новый участник",
                            person_email="new.user@turbo-don.ru",
                            sort_order=3,
                        )
                    ],
                ),
            ),
        )

    outlook_sync.assert_awaited_once()
    assert outlook_sync.await_args.kwargs["add_emails"] == ["new.user@turbo-don.ru"]
    assert outlook_sync.await_args.kwargs["remove_emails"] == []
    sync_card.assert_awaited_once_with(meeting_id)
    assert result.applied_changes.outlook_updated is True
    assert result.applied_changes.outlook_actions == ["participants_added"]
    assert meeting.outlook_changekey == "ck-2"


@pytest.mark.asyncio
async def test_update_removes_participant_calls_outlook_for_planned_series() -> None:
    db = AsyncMock()
    meeting_id = uuid.uuid4()
    position_id = uuid.uuid4()
    removed_user_id = uuid.uuid4()
    meeting = _meeting_stub(meeting_id=meeting_id, title="Серия", position_id=position_id)
    removed_position = SimpleNamespace(id=uuid.uuid4(), name="Удаляемая должность", is_active=True)
    meeting.participants.append(
        SimpleNamespace(
            id=uuid.uuid4(),
            user_id=removed_user_id,
            person_fio="Удаляемая должность",
            person_email="removed.user@turbo-don.ru",
            position_id=removed_position.id,
            position=removed_position,
            user=SimpleNamespace(full_name="Удаляемая должность", email="removed.user@turbo-don.ru"),
            sort_order=1,
            is_required=True,
        )
    )
    meeting.status = ScheduledMeetingStatus.PLANNED
    meeting.outlook_series_id = "series-1"
    meeting.outlook_changekey = "ck-1"

    execute_result = MagicMock()
    execute_result.scalar_one_or_none.return_value = meeting
    db.execute = AsyncMock(return_value=execute_result)
    db.flush = AsyncMock()

    from app.schemas.scheduled_meeting import ScheduledMeetingUpdate

    with (
        patch.object(
            ScheduledMeetingService,
            "_resolve_participant_create",
            AsyncMock(side_effect=lambda item: item),
        ),
        patch.object(
            ScheduledMeetingService,
            "_apply_participant_changes",
            AsyncMock(return_value=([], ["Удаляемая должность"])),
        ),
        patch.object(
            ScheduledMeetingService,
            "_resolve_emails_for_user_ids",
            AsyncMock(return_value=["removed.user@turbo-don.ru"]),
        ),
        patch(
            "app.services.scheduled_meeting_service.sync_series_participants_in_outlook",
            AsyncMock(return_value={"action": "participants_removed", "outlook_changekey": "ck-3"}),
        ) as outlook_sync,
        patch(
            "app.services.scheduled_meeting_registry_sync.ScheduledMeetingRegistrySyncService.sync_series_card",
            AsyncMock(),
        ) as sync_card,
    ):
        result = await ScheduledMeetingService(db).update(
            meeting_id,
            ScheduledMeetingUpdate(
                participants=_participants_payload(
                    meeting,
                    exclude_user_ids={removed_user_id},
                ),
            ),
        )

    outlook_sync.assert_awaited_once()
    assert outlook_sync.await_args.kwargs["add_emails"] == []
    assert outlook_sync.await_args.kwargs["remove_emails"] == ["removed.user@turbo-don.ru"]
    sync_card.assert_awaited_once_with(meeting_id)
    assert result.applied_changes.outlook_updated is True
    assert result.applied_changes.outlook_actions == ["participants_removed"]
    assert result.applied_changes.participants_removed == ["Удаляемая должность"]
    assert meeting.outlook_changekey == "ck-3"
