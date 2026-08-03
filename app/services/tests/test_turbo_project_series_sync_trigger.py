from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.services import turbo_project_series_sync_trigger as trigger


@pytest.fixture(autouse=True)
def _reset_trigger_state():
    trigger._memory_cooldown_until = 0.0
    yield
    trigger._memory_cooldown_until = 0.0


@pytest.mark.asyncio
async def test_maybe_sync_skips_when_disabled(monkeypatch) -> None:
    monkeypatch.setattr(trigger.settings, "TURBO_PROJECT_SERIES_SYNC_ENABLED", False)
    result = await trigger.maybe_sync_turbo_projects_on_schedule_open(AsyncMock())
    assert result == {"skipped": True, "reason": "sync_disabled"}


@pytest.mark.asyncio
async def test_maybe_sync_skips_on_cooldown(monkeypatch) -> None:
    monkeypatch.setattr(trigger.settings, "TURBO_PROJECT_SERIES_SYNC_ENABLED", True)
    monkeypatch.setattr(trigger.settings, "TURBO_PROJECT_SERIES_SYNC_ON_SCHEDULE_LIST", True)
    monkeypatch.setattr(trigger.settings, "TURBO_PROJECT_API_BASE_URL", "http://tp")
    monkeypatch.setattr(trigger.settings, "TURBO_PROJECT_EMAIL", "a@b.ru")
    monkeypatch.setattr(trigger.settings, "TURBO_PROJECT_PASSWORD", "x")

    with patch(
        "app.services.turbo_project_series_sync_trigger.meeting_redis_get",
        AsyncMock(return_value="1"),
    ):
        result = await trigger.maybe_sync_turbo_projects_on_schedule_open(AsyncMock())

    assert result == {"skipped": True, "reason": "cooldown"}


@pytest.mark.asyncio
async def test_maybe_sync_runs_discover_and_notify(monkeypatch) -> None:
    monkeypatch.setattr(trigger.settings, "TURBO_PROJECT_SERIES_SYNC_ENABLED", True)
    monkeypatch.setattr(trigger.settings, "TURBO_PROJECT_SERIES_SYNC_ON_SCHEDULE_LIST", True)
    monkeypatch.setattr(trigger.settings, "TURBO_PROJECT_API_BASE_URL", "http://tp")
    monkeypatch.setattr(trigger.settings, "TURBO_PROJECT_EMAIL", "a@b.ru")
    monkeypatch.setattr(trigger.settings, "TURBO_PROJECT_PASSWORD", "x")
    monkeypatch.setattr(trigger.settings, "TURBO_PROJECT_SERIES_SYNC_COOLDOWN_SECONDS", 300)

    sync_result = SimpleNamespace(
        as_dict=lambda: {"notified": 1, "notifications_created": 2, "failed": 0}
    )
    sync_service = AsyncMock()
    sync_service.discover_and_notify = AsyncMock(return_value=sync_result)

    with (
        patch(
            "app.services.turbo_project_series_sync_trigger.meeting_redis_get",
            AsyncMock(return_value=None),
        ),
        patch(
            "app.services.turbo_project_series_sync_trigger.meeting_redis_setex",
            AsyncMock(),
        ) as setex,
        patch(
            "app.services.turbo_project_series_sync_trigger.TurboProjectSeriesSyncService",
            return_value=sync_service,
        ),
    ):
        result = await trigger.maybe_sync_turbo_projects_on_schedule_open(AsyncMock())

    assert result["notified"] == 1
    assert result["notifications_created"] == 2
    setex.assert_awaited_once()
    sync_service.discover_and_notify.assert_awaited_once()
