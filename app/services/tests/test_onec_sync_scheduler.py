from __future__ import annotations

import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.services.onec_sync_scheduler import (
    next_onec_sync_at,
    run_onec_sync_with_lock,
    seconds_until_next_onec_sync,
)


MSK = ZoneInfo("Europe/Moscow")


@pytest.mark.asyncio
async def test_run_onec_sync_with_lock_skips_when_locked(monkeypatch):
    monkeypatch.setattr(
        "app.services.onec_sync_scheduler._try_acquire_sync_lock",
        lambda _owner: (None, False),
    )

    result = await run_onec_sync_with_lock(owner="test")

    assert result["ok"] is False
    assert result["status"] == "skipped_locked"


@pytest.mark.asyncio
async def test_run_onec_sync_with_lock_runs_daily_sync(monkeypatch):
    monkeypatch.setattr(
        "app.services.onec_sync_scheduler._try_acquire_sync_lock",
        lambda _owner: (None, True),
    )
    monkeypatch.setattr(
        "app.services.onec_sync_scheduler._release_sync_lock",
        lambda _client: None,
    )

    async def fake_daily_sync():
        return {"ok": True, "stock": {"ok": True}, "resource_specs": {"ok": True}}

    monkeypatch.setattr(
        "app.services.onec_daily_sync.run_onec_daily_sync",
        fake_daily_sync,
    )

    result = await run_onec_sync_with_lock(owner="test")

    assert result["ok"] is True
    assert result["status"] == "completed"


def test_next_onec_sync_at_hourly_on_the_hour(monkeypatch):
    monkeypatch.setattr("app.services.onec_sync_scheduler.settings.ONEC_SYNC_EVERY_HOURS", 1)
    monkeypatch.setattr("app.services.onec_sync_scheduler.settings.ONEC_SYNC_MINUTE", 0)

    now = datetime(2026, 8, 10, 8, 25, 30, tzinfo=MSK)
    assert next_onec_sync_at(now) == datetime(2026, 8, 10, 9, 0, 0, tzinfo=MSK)

    on_hour = datetime(2026, 8, 10, 9, 0, 0, tzinfo=MSK)
    assert next_onec_sync_at(on_hour) == on_hour

    after_hour = datetime(2026, 8, 10, 9, 0, 1, tzinfo=MSK)
    assert next_onec_sync_at(after_hour) == datetime(2026, 8, 10, 10, 0, 0, tzinfo=MSK)


def test_seconds_until_next_onec_sync(monkeypatch):
    monkeypatch.setattr("app.services.onec_sync_scheduler.settings.ONEC_SYNC_EVERY_HOURS", 1)
    monkeypatch.setattr("app.services.onec_sync_scheduler.settings.ONEC_SYNC_MINUTE", 0)

    now = datetime(2026, 8, 10, 8, 30, 0, tzinfo=MSK)
    assert seconds_until_next_onec_sync(now) == 30 * 60
