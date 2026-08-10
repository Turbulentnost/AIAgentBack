from unittest.mock import AsyncMock, patch

from app.workers.tasks import sync_onec_aveon_daily


def test_sync_onec_aveon_daily_disabled(monkeypatch) -> None:
    monkeypatch.setattr("app.core.config.settings.ONEC_DAILY_SYNC_ENABLED", False)
    result = sync_onec_aveon_daily()
    assert result["status"] == "disabled"


def test_sync_onec_aveon_daily_runs_sync(monkeypatch) -> None:
    monkeypatch.setattr("app.core.config.settings.ONEC_DAILY_SYNC_ENABLED", True)

    class FakeRedis:
        def __init__(self, *args, **kwargs):
            pass

        def set(self, *args, **kwargs):
            return True

        def delete(self, *args, **kwargs):
            return 1

        def close(self):
            return None

    monkeypatch.setattr("app.workers.tasks.Redis", FakeRedis)

    with patch(
        "app.workers.tasks._run_async_task",
        return_value={
            "celery_task_id": "task-1",
            "task_name": "sync_onec_aveon_daily",
            "status": "completed",
            "ok": True,
            "stock": {"ok": True},
            "resource_specs": {"ok": True},
        },
    ) as run_async:
        with patch(
            "app.services.onec_daily_sync.run_onec_daily_sync",
            AsyncMock(return_value={"ok": True, "stock": {"ok": True}, "resource_specs": {"ok": True}}),
        ):
            result = sync_onec_aveon_daily()

    run_async.assert_called_once()
    assert result["status"] == "completed"
    assert result["ok"] is True
