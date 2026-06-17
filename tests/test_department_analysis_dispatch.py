from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.enums import DepartmentAnalysisRunStatus
from app.models.nd_control_analysis import DepartmentAnalysisRun
from app.services.department_analysis_dispatch import (
    enqueue_department_analysis_run,
    is_celery_department_analysis_available,
    is_stale_pending_run,
    maybe_recover_stale_pending_run,
)


def _pending_run(*, age_seconds: int = 60) -> DepartmentAnalysisRun:
    return DepartmentAnalysisRun(
        id=uuid.uuid4(),
        department_id=uuid.uuid4(),
        status=DepartmentAnalysisRunStatus.PENDING,
        created_at=datetime.now(timezone.utc) - timedelta(seconds=age_seconds),
    )


def test_is_stale_pending_run_true_for_old_pending() -> None:
    assert is_stale_pending_run(_pending_run(age_seconds=60)) is True


def test_is_stale_pending_run_false_for_recent_pending() -> None:
    assert is_stale_pending_run(_pending_run(age_seconds=5)) is False


def test_is_stale_pending_run_false_when_started() -> None:
    run = _pending_run(age_seconds=60)
    run.started_at = datetime.now(timezone.utc)
    assert is_stale_pending_run(run) is False


@patch("app.services.department_analysis_dispatch.is_celery_department_analysis_available", return_value=False)
@pytest.mark.asyncio
async def test_enqueue_uses_background_when_celery_unavailable(_mock_celery: MagicMock) -> None:
    db = AsyncMock()
    run = _pending_run()
    background_tasks = MagicMock()

    await enqueue_department_analysis_run(db, run, False, background_tasks)

    background_tasks.add_task.assert_called_once()
    db.flush.assert_not_called()


@patch("app.services.department_analysis_dispatch.enqueue_department_analysis_run", new_callable=AsyncMock)
@pytest.mark.asyncio
async def test_maybe_recover_stale_pending_run(mock_enqueue: AsyncMock) -> None:
    db = AsyncMock()
    run = _pending_run(age_seconds=120)
    background_tasks = MagicMock()

    recovered = await maybe_recover_stale_pending_run(db, run, background_tasks)

    assert recovered is True
    mock_enqueue.assert_awaited_once()


def test_is_celery_department_analysis_available_when_registered() -> None:
    mock_inspect = MagicMock()
    mock_inspect.registered.return_value = {
        "worker1": ["run_department_analysis", "debug_task"],
    }
    with patch("app.workers.celery_app.celery_app") as celery_app:
        celery_app.control.inspect.return_value = mock_inspect
        assert is_celery_department_analysis_available() is True
