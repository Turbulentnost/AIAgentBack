from __future__ import annotations

import uuid
from datetime import date, time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.enums import AppNotificationType, ScheduledMeetingWeekday
from app.schemas.app_notification import (
    AppNotificationAcceptRequest,
    TurboProjectRgParticipantProposal,
    TurboProjectRgSeriesProposal,
    TurboProjectRgWeeklySlotProposal,
)
from app.services.app_notification_service import (
    AppNotificationService,
    AppNotificationServiceError,
)


def _notification(**kwargs):
    base = {
        "id": uuid.uuid4(),
        "user_id": uuid.uuid4(),
        "type": AppNotificationType.TURBO_PROJECT_RG.value,
        "title": "Новый проект",
        "body": "Нужна РГ",
        "entity_key": "turbo_project:153",
        "payload": {"file_id": 153, "project_name": "Demo"},
        "read_at": None,
        "opened_at": None,
        "resolved_at": None,
        "created_at": None,
    }
    base.update(kwargs)
    return SimpleNamespace(**base)


def _proposal() -> TurboProjectRgSeriesProposal:
    manager = TurboProjectRgParticipantProposal(
        user_id=uuid.uuid4(),
        fio="РП",
        email="rp@turbo-don.ru",
    )
    return TurboProjectRgSeriesProposal(
        file_id=153,
        project_name="Demo",
        one_c_ref_key="ref",
        nomer_proekta="P-1",
        status_proekta="ВРаботе",
        title="РГ: Demo",
        meeting_category_name="РГ по проекту",
        series_start_date=date(2026, 2, 20),
        series_end_date=date(2026, 12, 4),
        recurrence_label="еженедельно",
        weekly_slot=TurboProjectRgWeeklySlotProposal(
            weekday=ScheduledMeetingWeekday.MONDAY,
            time_local=time(10, 0),
        ),
        manager=manager,
        responsible=manager,
        participants=[],
    )


@pytest.mark.asyncio
async def test_open_builds_proposal() -> None:
    user = SimpleNamespace(id=uuid.uuid4())
    notification = _notification(user_id=user.id)
    db = AsyncMock()
    db.get = AsyncMock(return_value=notification)
    db.flush = AsyncMock()
    service = AppNotificationService(db)
    proposal = _proposal()

    with patch(
        "app.services.app_notification_service.TurboProjectSeriesSyncService"
    ) as sync_cls:
        sync_cls.return_value.build_series_proposal = AsyncMock(return_value=proposal)
        result = await service.open(notification.id, user)

    assert result.proposal is not None
    assert result.proposal.file_id == 153
    assert notification.read_at is not None
    assert notification.opened_at is not None


@pytest.mark.asyncio
async def test_accept_creates_series_and_resolves_related() -> None:
    user = SimpleNamespace(id=uuid.uuid4())
    notification = _notification(user_id=user.id)
    related = _notification(
        user_id=uuid.uuid4(),
        entity_key=notification.entity_key,
    )
    db = AsyncMock()
    db.get = AsyncMock(return_value=notification)
    db.flush = AsyncMock()
    db.refresh = AsyncMock()
    related_result = MagicMock()
    related_result.scalars.return_value.all.return_value = [notification, related]
    db.execute = AsyncMock(return_value=related_result)

    meeting = SimpleNamespace(id=uuid.uuid4(), title="РГ: Demo")
    proposal = _proposal()
    service = AppNotificationService(db)
    expected = SimpleNamespace(notification=notification, scheduled_meeting=meeting)

    with (
        patch(
            "app.services.app_notification_service.TurboProjectSeriesSyncService"
        ) as sync_cls,
        patch(
            "app.services.app_notification_service.AppNotificationAcceptRead",
            return_value=expected,
        ),
    ):
        sync_cls.return_value.build_series_proposal = AsyncMock(return_value=proposal)
        sync_cls.return_value.create_series_from_proposal = AsyncMock(return_value=meeting)
        result = await service.accept(
            notification.id,
            user,
            AppNotificationAcceptRequest(weekday=ScheduledMeetingWeekday.TUESDAY),
        )

    assert result is expected
    assert notification.resolved_at is not None
    assert related.resolved_at is not None
    sync_cls.return_value.create_series_from_proposal.assert_awaited_once()


@pytest.mark.asyncio
async def test_accept_rejects_other_types() -> None:
    user = SimpleNamespace(id=uuid.uuid4())
    notification = _notification(user_id=user.id, type="other")
    db = AsyncMock()
    db.get = AsyncMock(return_value=notification)
    service = AppNotificationService(db)

    with pytest.raises(AppNotificationServiceError, match="не связано"):
        await service.accept(notification.id, user)
