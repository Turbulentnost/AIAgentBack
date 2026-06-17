from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.enums import TaskStatus
from app.schemas.meeting import MeetingRunCreate, MeetingSlotsRequest
from app.services.meeting_service import MeetingService, MeetingServiceError


@pytest.fixture
def user():
    return SimpleNamespace(id=uuid.uuid4(), is_superuser=False)


@pytest.mark.asyncio
async def test_run_creates_task_and_enqueues(user) -> None:
    db = AsyncMock()
    agent = SimpleNamespace(id=uuid.uuid4(), slug="meeting_agent")
    db.scalar = AsyncMock(return_value=agent)

    def _add(task):
        task.id = uuid.uuid4()

    db.add = MagicMock(side_effect=_add)
    db.flush = AsyncMock()

    service = MeetingService(db)
    service._ensure_access = AsyncMock()  # type: ignore[method-assign]
    service.audit.log = AsyncMock()

    celery_result = MagicMock(id="celery-123")
    with patch("app.workers.tasks.run_meeting_task.apply_async", return_value=celery_result) as enqueue:
        payload = MeetingRunCreate(memo_number="0001")
        result = await service.run(payload, current_user=user)

    assert result.celery_task_id == "celery-123"
    assert result.status == TaskStatus.PENDING.value
    enqueue.assert_called_once_with(args=[str(result.task_id)], queue="agents")
    db.add.assert_called_once()


@pytest.mark.asyncio
async def test_find_slots_requires_access(user) -> None:
    db = AsyncMock()
    service = MeetingService(db)
    service._ensure_access = AsyncMock(side_effect=MeetingServiceError("Нет доступа"))  # type: ignore[method-assign]

    with pytest.raises(MeetingServiceError, match="Нет доступа"):
        await service.find_slots(MeetingSlotsRequest(memo_number="1"), current_user=user)


@pytest.mark.asyncio
async def test_get_run_returns_task_result(user) -> None:
    db = AsyncMock()
    task_id = uuid.uuid4()
    task = SimpleNamespace(
        id=task_id,
        task_type="meeting",
        status=TaskStatus.COMPLETED,
        requires_human_review=True,
        error_message=None,
        final_result={"summary": "Готово"},
    )
    result = SimpleNamespace(
        summary="Готово",
        raw_output={"status": "completed"},
        additional_data=None,
    )

    db.get = AsyncMock(return_value=task)
    service = MeetingService(db)
    service._ensure_access = AsyncMock()  # type: ignore[method-assign]

    with patch.object(MeetingService, "__init__", lambda self, db: None):
        pass

    from app.services import meeting_service as module

    with (
        patch.object(module.PermissionService, "can_access_task", AsyncMock(return_value=True)),
        patch.object(module.TaskService, "get_current_result", AsyncMock(return_value=result)),
    ):
        service = MeetingService(db)
        service._ensure_access = AsyncMock()
        service.db = db
        read = await service.get_run(task_id, current_user=user)

    assert read.task_id == task_id
    assert read.summary == "Готово"
    assert read.result == {"status": "completed"}
