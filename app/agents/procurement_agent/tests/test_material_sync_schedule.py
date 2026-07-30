import pytest

from app.core.config import settings

pytest.importorskip("celery")

from app.workers.celery_app import celery_app  # noqa: E402


def test_material_order_sync_uses_one_periodic_task() -> None:
    schedule = celery_app.conf.beat_schedule
    if not settings.PROCUREMENT_ORCHESTRATOR_ENABLED:
        assert "sync-procurement-material-orders" not in schedule
        return

    entry = schedule["sync-procurement-material-orders"]
    assert entry["task"] == "sync_procurement_material_orders"
    assert entry["schedule"] == float(
        settings.PROCUREMENT_ORCHESTRATOR_INTERVAL_SECONDS
    )
    assert settings.PROCUREMENT_ORCHESTRATOR_INTERVAL_SECONDS == 1800
    assert "poll-procurement-sources" not in schedule
    assert "reconcile-procurement-supplier-orders" not in schedule
