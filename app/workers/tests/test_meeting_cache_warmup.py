import asyncio
from unittest.mock import AsyncMock, patch

from app.workers.tasks import warm_meeting_dashboard_cache


def test_warm_meeting_dashboard_cache_task(monkeypatch) -> None:
    monkeypatch.setattr("app.core.config.settings.MEETING_DASHBOARD_CACHE_ENABLED", True)
    monkeypatch.setattr("app.core.config.settings.MEETING_DASHBOARD_CACHE_WARMUP_ENABLED", True)

    with patch(
        "app.workers.tasks._run_async_task",
        side_effect=lambda factory: asyncio.run(factory()),
    ) as run_async:
        with patch(
            "app.services.meeting_dashboard_cache.MeetingDashboardCacheService.warmup",
            AsyncMock(return_value={
                "date": "2026-06-17",
                "fetched_at": "2026-06-17T06:00:00+00:00",
                "from_cache": False,
                "error": None,
                "counts": {"today": 1, "unapproved": 2},
            }),
        ):
            result = warm_meeting_dashboard_cache()

    run_async.assert_called_once()
    assert result["date"] == "2026-06-17"
    assert result["counts"]["today"] == 1


def test_warm_meeting_dashboard_cache_skipped_when_disabled(monkeypatch) -> None:
    monkeypatch.setattr("app.core.config.settings.MEETING_DASHBOARD_CACHE_ENABLED", False)
    result = warm_meeting_dashboard_cache()
    assert result["skipped"] is True
