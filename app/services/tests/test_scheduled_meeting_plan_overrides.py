from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta
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
    ScheduledMeetingPlanOverride,
    ScheduledMeetingPlanRequest,
)
from app.services.scheduled_meeting_occurrences import SeriesOccurrence
from app.services.scheduled_meeting_plan_overrides import apply_plan_overrides
from app.services.scheduled_meeting_service import ScheduledMeetingService


TZ = ZoneInfo("Europe/Moscow")


def _occurrence(day: date) -> SeriesOccurrence:
    start = datetime.combine(day, time(9, 0), tzinfo=TZ)
    return SeriesOccurrence(
        occurrence_date=day,
        slot_start=start,
        slot_end=start + timedelta(hours=1),
        outlook_item_id=None,
        outlook_changekey=None,
        subject="Проектная серия",
        is_cancelled=False,
        source="rule",
    )


def _meeting(**kwargs) -> SimpleNamespace:
    defaults = {
        "id": uuid.uuid4(),
        "title": "Проектная серия",
        "status": ScheduledMeetingStatus.CREATED,
        "outlook_series_id": None,
        "time_local": time(9, 0),
        "duration_minutes": 60,
        "frequency": ScheduledMeetingFrequency.WEEKLY,
        "interval": 1,
        "monthly_mode": None,
        "day_of_month": None,
        "weekday": ScheduledMeetingWeekday.WEDNESDAY,
        "weekday_position": None,
        "series_start_date": date(2026, 7, 15),
        "series_end_date": date(2026, 8, 12),
        "meeting_type": ScheduledMeetingType.PLANNED,
        "meeting_category_id": uuid.uuid4(),
        "meeting_category": SimpleNamespace(
            id=uuid.uuid4(), name="Комитет", sort_order=1, is_active=True
        ),
        "manager_user_id": uuid.uuid4(),
        "manager_user": SimpleNamespace(
            id=uuid.uuid4(), full_name="Директор", email="director@turbo-don.ru"
        ),
        "responsible_user_id": uuid.uuid4(),
        "responsible_user": SimpleNamespace(
            id=uuid.uuid4(), full_name="Секретарь", email="secretary@turbo-don.ru"
        ),
        "manager_position_id": uuid.uuid4(),
        "manager_position": SimpleNamespace(id=uuid.uuid4(), name="Директор", is_active=True),
        "responsible_position_id": uuid.uuid4(),
        "responsible_position": SimpleNamespace(
            id=uuid.uuid4(), name="Секретарь", is_active=True
        ),
        "recurrence_label": "еженедельно",
        "recurrence_rule": {},
        "outlook_changekey": None,
        "outlook_meeting_url": None,
        "payload": None,
        "participants": [],
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


@pytest.mark.asyncio
async def test_apply_shift_calls_reschedule_occurrence() -> None:
    meeting = _meeting()
    day = date(2026, 7, 15)
    with (
        patch(
            "app.services.scheduled_meeting_plan_overrides.build_occurrences_from_rule",
            return_value=[_occurrence(day)],
        ),
        patch(
            "app.services.scheduled_meeting_plan_overrides.dispatch_reschedule_meeting",
            return_value={"status": "rescheduled"},
        ) as mock_reschedule,
        patch(
            "app.services.scheduled_meeting_plan_overrides.asyncio.to_thread",
            new=AsyncMock(side_effect=lambda fn, *args, **kwargs: fn(*args, **kwargs)),
        ),
    ):
        await apply_plan_overrides(
            meeting,
            [
                ScheduledMeetingPlanOverride(
                    occurrence_date=day,
                    action="shift",
                    new_start="2026-07-16 10:00",
                )
            ],
        )

    kwargs = mock_reschedule.call_args.kwargs
    assert kwargs["subject"] == "Проектная серия"
    assert kwargs["start"] == "2026-07-15 09:00"
    assert kwargs["new_start"] == "2026-07-16 10:00"
    assert kwargs["reschedule_scope"] == "occurrence"


@pytest.mark.asyncio
async def test_apply_skip_calls_cancel_occurrence() -> None:
    meeting = _meeting()
    day = date(2026, 7, 15)
    with (
        patch(
            "app.services.scheduled_meeting_plan_overrides.build_occurrences_from_rule",
            return_value=[_occurrence(day)],
        ),
        patch(
            "app.services.scheduled_meeting_plan_overrides.dispatch_cancel_meeting",
            return_value={"status": "cancelled"},
        ) as mock_cancel,
        patch(
            "app.services.scheduled_meeting_plan_overrides.asyncio.to_thread",
            new=AsyncMock(side_effect=lambda fn, *args, **kwargs: fn(*args, **kwargs)),
        ),
    ):
        await apply_plan_overrides(
            meeting,
            [ScheduledMeetingPlanOverride(occurrence_date=day, action="skip")],
        )

    kwargs = mock_cancel.call_args.kwargs
    assert kwargs["subject"] == "Проектная серия"
    assert kwargs["start"] == "2026-07-15 09:00"
    assert kwargs["cancel_scope"] == "occurrence"


@pytest.mark.asyncio
async def test_plan_without_body_skips_overrides() -> None:
    db = AsyncMock()
    meeting_id = uuid.uuid4()
    meeting = _meeting(id=meeting_id)
    loaded = MagicMock()
    loaded.scalar_one_or_none.return_value = meeting
    db.execute = AsyncMock(return_value=loaded)

    with (
        patch(
            "app.services.scheduled_meeting_service.plan_scheduled_meeting_in_outlook",
            AsyncMock(),
        ) as plan_outlook,
        patch(
            "app.services.scheduled_meeting_plan_overrides.apply_plan_overrides",
            AsyncMock(),
        ) as apply_overrides,
        patch(
            "app.services.scheduled_meeting_registry_sync.ScheduledMeetingRegistrySyncService.sync_series_card",
            AsyncMock(),
        ),
    ):
        await ScheduledMeetingService(db).plan(meeting_id)

    plan_outlook.assert_awaited_once()
    apply_overrides.assert_not_awaited()


@pytest.mark.asyncio
async def test_plan_with_shift_override_applies_after_outlook_create() -> None:
    db = AsyncMock()
    meeting_id = uuid.uuid4()
    meeting = _meeting(id=meeting_id)
    loaded = MagicMock()
    loaded.scalar_one_or_none.return_value = meeting
    db.execute = AsyncMock(return_value=loaded)
    day = date(2026, 7, 15)

    with (
        patch(
            "app.services.scheduled_meeting_service.plan_scheduled_meeting_in_outlook",
            AsyncMock(),
        ) as plan_outlook,
        patch(
            "app.services.scheduled_meeting_plan_overrides.apply_plan_overrides",
            AsyncMock(),
        ) as apply_overrides,
        patch(
            "app.services.scheduled_meeting_registry_sync.ScheduledMeetingRegistrySyncService.sync_series_card",
            AsyncMock(),
        ),
    ):
        await ScheduledMeetingService(db).plan(
            meeting_id,
            ScheduledMeetingPlanRequest(
                conflict_policy="soft_week",
                overrides=[
                    ScheduledMeetingPlanOverride(
                        occurrence_date=day,
                        action="shift",
                        new_start="2026-07-16 10:00",
                    )
                ],
            ),
        )

    plan_outlook.assert_awaited_once()
    apply_overrides.assert_awaited_once()
    assert apply_overrides.await_args.args[0] is meeting
    assert apply_overrides.await_args.args[1][0].action == "shift"
