from __future__ import annotations

import json
from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app.services.meeting_dashboard_cache import (
    MeetingDashboardCacheService,
    _cache_key,
    _is_usable_cache,
)


@pytest.fixture
def sample_payload() -> dict:
    card = {
        "number": "000009853",
        "ref_key": "11111111-1111-1111-1111-111111111111",
        "initiator": {"full_name": "Иванов И.И."},
        "manager": {"full_name": "Петров П.П."},
        "ТекстСлужебнойЗаписки": "Текст служебной записки",
    }
    return {
        "date": "2026-06-17",
        "unapproved": [card],
        "today": [card],
        "items": [card],
        "counts": {"unapproved": 1, "today": 1, "items": 1},
    }


@pytest.mark.asyncio
async def test_get_dashboard_returns_cache_hit(sample_payload) -> None:
    fetched_at = datetime(2026, 6, 17, 10, 0, tzinfo=timezone.utc)
    cached = {**sample_payload, "fetched_at": fetched_at, "fetch_ok": True}
    service = MeetingDashboardCacheService()

    with patch.object(service, "_read_cache", AsyncMock(return_value=cached)) as read_cache:
        with patch("app.services.meeting_dashboard_cache.asyncio.to_thread") as fetch:
            payload, result_fetched_at, from_cache = await service.get_dashboard(
                target_date=date(2026, 6, 17),
            )

    fetch.assert_not_called()
    read_cache.assert_awaited_once_with(date(2026, 6, 17))
    assert from_cache is True
    assert payload == sample_payload
    assert result_fetched_at == fetched_at


@pytest.mark.asyncio
async def test_get_dashboard_fetches_on_cache_miss(sample_payload) -> None:
    fetched_at = datetime(2026, 6, 17, 11, 0, tzinfo=timezone.utc)
    service = MeetingDashboardCacheService()

    with patch.object(service, "_read_cache", AsyncMock(return_value=None)):
        with patch.object(
            service,
            "_fetch_and_store",
            AsyncMock(return_value=(sample_payload, fetched_at)),
        ) as fetch_and_store:
            payload, result_fetched_at, from_cache = await service.get_dashboard(
                target_date=date(2026, 6, 17),
            )

    fetch_and_store.assert_awaited_once_with(date(2026, 6, 17))
    assert from_cache is False
    assert payload == sample_payload
    assert result_fetched_at == fetched_at


@pytest.mark.asyncio
async def test_refresh_dashboard_returns_cache_on_fetch_error(sample_payload) -> None:
    fetched_at = datetime(2026, 6, 17, 9, 30, tzinfo=timezone.utc)
    cached = {**sample_payload, "fetched_at": fetched_at, "fetch_ok": True}
    service = MeetingDashboardCacheService()

    with patch.object(
        service,
        "_fetch_and_store",
        AsyncMock(side_effect=RuntimeError("HTTP 401")),
    ):
        with patch.object(service, "_read_cache", AsyncMock(return_value=cached)):
            payload, result_fetched_at, from_cache, error = await service.refresh_dashboard(
                target_date=date(2026, 6, 17),
            )

    assert from_cache is True
    assert error == "HTTP 401"
    assert payload == sample_payload
    assert result_fetched_at == fetched_at


@pytest.mark.asyncio
async def test_refresh_dashboard_raises_without_cache() -> None:
    service = MeetingDashboardCacheService()

    with patch.object(
        service,
        "_fetch_and_store",
        AsyncMock(side_effect=RuntimeError("HTTP 401")),
    ):
        with patch.object(service, "_read_cache", AsyncMock(return_value=None)):
            with pytest.raises(RuntimeError, match="HTTP 401"):
                await service.refresh_dashboard(target_date=date(2026, 6, 17))


def test_cache_key_uses_iso_date() -> None:
    assert _cache_key(date(2026, 6, 17)) == "meeting:dashboard:2026-06-17"


def test_is_usable_cache_rejects_legacy_empty_snapshot() -> None:
    assert _is_usable_cache({"counts": {"unapproved": 0, "today": 0}}) is False


def test_is_usable_cache_accepts_flagged_empty_snapshot() -> None:
    assert _is_usable_cache({"fetch_ok": True, "counts": {"unapproved": 0, "today": 0}}) is True


@pytest.mark.asyncio
async def test_get_dashboard_ignores_legacy_empty_cache(sample_payload) -> None:
    fetched_at = datetime(2026, 6, 17, 10, 0, tzinfo=timezone.utc)
    stale_empty = {
        **sample_payload,
        "unapproved": [],
        "today": [],
        "counts": {"unapproved": 0, "today": 0},
        "fetched_at": fetched_at,
    }
    service = MeetingDashboardCacheService()

    with patch.object(service, "_read_cache", AsyncMock(return_value=stale_empty)):
        with patch.object(
            service,
            "_fetch_and_store",
            AsyncMock(return_value=(sample_payload, fetched_at)),
        ) as fetch_and_store:
            payload, _, from_cache = await service.get_dashboard(target_date=date(2026, 6, 17))

    fetch_and_store.assert_awaited_once()
    assert from_cache is False
    assert payload == sample_payload


@pytest.mark.asyncio
async def test_warmup_calls_refresh(sample_payload) -> None:
    fetched_at = datetime(2026, 6, 17, 6, 0, tzinfo=timezone.utc)
    service = MeetingDashboardCacheService()

    with patch.object(
        service,
        "refresh_dashboard",
        AsyncMock(return_value=(sample_payload, fetched_at, False, None)),
    ) as refresh:
        result = await service.warmup(target_date=date(2026, 6, 17))

    refresh.assert_awaited_once_with(target_date=date(2026, 6, 17))
    assert result["date"] == "2026-06-17"
    assert result["from_cache"] is False
    assert result["counts"] == sample_payload["counts"]

