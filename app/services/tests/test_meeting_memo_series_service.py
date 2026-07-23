from __future__ import annotations

import uuid
from datetime import date, time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.enums import ScheduledMeetingFrequency, ScheduledMeetingStatus, ScheduledMeetingType
from app.services.meeting_memo_recurrence import MemoRecurrenceDraft
from app.services.meeting_memo_series_service import (
    MeetingMemoSeriesService,
    MeetingMemoSeriesServiceError,
)
from app.services.scheduled_meeting_recurrence import RecurrenceInput


def _recurrence() -> RecurrenceInput:
    return RecurrenceInput(
        frequency=ScheduledMeetingFrequency.DAILY,
        interval=1,
        time_local=time(13, 15),
        duration_minutes=45,
        series_start_date=date(2026, 7, 24),
        series_end_date=date(2026, 8, 6),
    )


def _memo_detail() -> dict:
    return {
        "ref_key": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        "number": "000011832",
        "title": "Технический совет — ежедневный соз соз соз",
        "application": {
            "manager": {
                "full_name": "Иванов Иван Иванович",
                "position": "Технический директор",
            },
            "initiator": {
                "full_name": "Петров Петр Петрович",
                "position": "Помощник технического директора",
            },
            "participants": [
                {"full_name": "Сидоров Сидор Сидорович", "position": "Главный инженер"},
            ],
            "meeting_type": "Плановое",
            "agenda": "Обсуждение проекта",
        },
        "queue": {},
    }


def _user() -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), full_name="УД Тест")


def test_resolve_series_title_uses_existing_topic_description() -> None:
    service = MeetingMemoSeriesService(AsyncMock())
    title = service._resolve_series_title(
        {"title": "Тема из СЗ"},
        {
            "ref_key": "topic-ref",
            "description": "Технический совет",
            "used_existing": True,
        },
    )
    assert title == "Технический совет"


@pytest.mark.asyncio
async def test_create_series_from_memo_rejects_low_confidence() -> None:
    db = AsyncMock()
    service = MeetingMemoSeriesService(db)
    draft = MemoRecurrenceDraft(is_series=True, confidence="low", recurrence=_recurrence())

    with patch.object(service, "_find_existing_series", AsyncMock(return_value=None)):
        with patch(
            "app.services.meeting_memo_series_service.MeetingMemoCacheService"
        ) as cache_cls:
            cache_cls.return_value.get_memo_detail = AsyncMock(
                return_value=(_memo_detail(), None, True)
            )
            with patch(
                "app.services.meeting_memo_series_service.resolve_memo_recurrence_async",
                AsyncMock(return_value=draft),
            ):
                with pytest.raises(MeetingMemoSeriesServiceError) as exc:
                    await service.create_series_from_memo(
                        "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                        current_user=_user(),
                    )
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_create_series_from_memo_creates_scheduled_meeting_and_approves_memo() -> None:
    db = AsyncMock()
    manager_user_id = uuid.uuid4()
    responsible_user_id = uuid.uuid4()
    participant_user_id = uuid.uuid4()
    manager_position_id = uuid.uuid4()
    responsible_position_id = uuid.uuid4()
    participant_position_id = uuid.uuid4()
    category_id = uuid.uuid4()
    meeting_id = uuid.uuid4()

    service = MeetingMemoSeriesService(db)
    draft = MemoRecurrenceDraft(
        is_series=True,
        confidence="high",
        recurrence=_recurrence(),
        recurrence_label="ежедневно, 13:15 · до 06.08.2026, 14 встреч",
        occurrence_count=14,
    )

    category = SimpleNamespace(id=category_id, name="Технический совет", sort_order=1)
    created_read = SimpleNamespace(
        id=meeting_id,
        title="Технический совет",
        recurrence_label=draft.recurrence_label,
        occurrence_count=14,
        status=ScheduledMeetingStatus.CREATED,
    )
    approve_read = SimpleNamespace(changed=True, already_approved=False, message="СЗ согласована")

    with patch.object(service, "_find_existing_series", AsyncMock(return_value=None)):
        with patch.object(
            service,
            "_resolve_meeting_category",
            AsyncMock(return_value=category),
        ):
            with patch.object(
                service,
                "_resolve_person_for_memo_participant",
                AsyncMock(
                    side_effect=[
                        SimpleNamespace(
                            user_id=manager_user_id,
                            fio="Иванов Иван Иванович",
                            email="ivanov@turbo-don.ru",
                            position_id=manager_position_id,
                        ),
                        SimpleNamespace(
                            user_id=responsible_user_id,
                            fio="Петров Петр Петрович",
                            email="petrov@turbo-don.ru",
                            position_id=responsible_position_id,
                        ),
                        SimpleNamespace(
                            user_id=participant_user_id,
                            fio="Сидоров Сидор Сидорович",
                            email="sidorov@turbo-don.ru",
                            position_id=participant_position_id,
                        ),
                    ]
                ),
            ):
                with patch(
                    "app.services.meeting_memo_series_service.MeetingMemoCacheService"
                ) as cache_cls:
                    cache = cache_cls.return_value
                    cache.get_memo_detail = AsyncMock(
                        return_value=(_memo_detail(), None, True)
                    )
                    cache.set_series_planning_choice = AsyncMock()
                    with patch(
                        "app.services.meeting_memo_series_service.resolve_memo_recurrence_async",
                        AsyncMock(return_value=draft),
                    ):
                        with patch(
                            "app.services.meeting_memo_series_service.ScheduledMeetingService"
                        ) as scheduled_cls:
                            scheduled_cls.return_value.create = AsyncMock(
                                return_value=created_read
                            )
                            with patch(
                                "app.services.meeting_memo_series_service.MeetingService"
                            ) as meeting_service_cls:
                                meeting_service_cls.return_value.approve_memo = AsyncMock(
                                    return_value=approve_read
                                )
                                result = await service.create_series_from_memo(
                                    "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                                    current_user=_user(),
                                )

    assert result.scheduled_meeting.id == meeting_id
    assert result.occurrence_count == 14
    assert result.memo_approved is True
    scheduled_cls.return_value.create.assert_awaited_once()
    scheduled_cls.return_value.plan.assert_not_called()
    meeting_service_cls.return_value.approve_memo.assert_awaited_once()
    payload = scheduled_cls.return_value.create.await_args.args[0]
    assert payload.meeting_type == ScheduledMeetingType.PLANNED
    assert payload.status == ScheduledMeetingStatus.CREATED
    assert payload.meeting_category_id == category_id
    assert payload.manager_user_id == manager_user_id
    assert payload.responsible_user_id == responsible_user_id
    assert payload.recurrence_label == draft.recurrence_label
    assert payload.participants[0].user_id == participant_user_id
    assert payload.payload["memo_ref_key"] == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    cache.set_series_planning_choice.assert_awaited_once_with(
        "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        "series",
    )
