from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app.services.meeting_memo_cache import (
    MeetingMemoCacheService,
    MemoCacheMissError,
    _cache_key,
    build_detail_from_dashboard_item,
    collect_memo_ref_keys,
    detail_is_agent_ready,
)


@pytest.fixture
def sample_detail() -> dict:
    return {
        "ref_key": "11111111-1111-1111-1111-111111111111",
        "number": "000008622",
        "title": "Прошу назначить совещание",
        "queue": {"ref_key": "11111111-1111-1111-1111-111111111111"},
        "application": {"participants": []},
    }


@pytest.fixture
def agent_ready_detail(sample_detail) -> dict:
    detail = dict(sample_detail)
    detail["application"] = {
        "initiator": {"full_name": "Иванов И.И.", "email": "ivanov@turbo-don.ru"},
        "manager": {"full_name": "Иванов И.И.", "email": "ivanov@turbo-don.ru"},
        "participants": [
            {"full_name": "Петров П.П.", "email": "petrov@turbo-don.ru", "ref_key": "abc"},
        ],
    }
    return detail


@pytest.mark.asyncio
async def test_get_memo_detail_returns_cache_hit(sample_detail) -> None:
    fetched_at = datetime(2026, 6, 18, 10, 0, tzinfo=timezone.utc)
    cached = {"payload": sample_detail, "fetched_at": fetched_at}
    service = MeetingMemoCacheService()
    ref_key = "11111111-1111-1111-1111-111111111111"

    with patch.object(service, "_read_cache", AsyncMock(return_value=cached)) as read_cache:
        with patch.object(service, "_fetch_and_store", AsyncMock()) as fetch:
            payload, result_fetched_at, from_cache = await service.get_memo_detail(ref_key)

    fetch.assert_not_called()
    read_cache.assert_awaited_once_with(ref_key)
    assert from_cache is True
    assert payload == sample_detail
    assert result_fetched_at == fetched_at


@pytest.mark.asyncio
async def test_get_memo_detail_raises_on_cache_miss(sample_detail) -> None:
    service = MeetingMemoCacheService()
    ref_key = "11111111-1111-1111-1111-111111111111"

    with patch.object(service, "_read_cache", AsyncMock(return_value=None)):
        with patch.object(service, "_fetch_and_store", AsyncMock()) as fetch:
            with pytest.raises(MemoCacheMissError):
                await service.get_memo_detail(ref_key)

    fetch.assert_not_called()


@pytest.mark.asyncio
async def test_get_memo_detail_force_refresh_hits_onec(sample_detail) -> None:
    fetched_at = datetime(2026, 6, 18, 11, 0, tzinfo=timezone.utc)
    service = MeetingMemoCacheService()
    ref_key = "11111111-1111-1111-1111-111111111111"

    with patch.object(service, "_read_cache", AsyncMock()) as read_cache:
        with patch.object(
            service,
            "_fetch_and_store",
            AsyncMock(return_value=(sample_detail, fetched_at)),
        ) as fetch_and_store:
            payload, result_fetched_at, from_cache = await service.get_memo_detail(
                ref_key,
                force_refresh=True,
            )

    read_cache.assert_not_called()
    fetch_and_store.assert_awaited_once_with(ref_key)
    assert from_cache is False
    assert payload == sample_detail
    assert result_fetched_at == fetched_at


def test_cache_key_normalizes_ref_key() -> None:
    assert _cache_key("11111111-1111-1111-1111-111111111111") == (
        "meeting:memo:11111111-1111-1111-1111-111111111111"
    )


def test_collect_memo_ref_keys_deduplicates_dashboard_items() -> None:
    payload = {
        "unapproved": [{"ref_key": "aaa-bbbb"}, {"ref_key": "ccc-dddd"}],
        "today": [{"ref_key": "AAA-BBBB"}],
    }
    assert collect_memo_ref_keys(payload) == ["aaa-bbbb", "ccc-dddd"]


def test_build_detail_from_dashboard_item_uses_queue_fields() -> None:
    item = {
        "ref_key": "11111111-1111-1111-1111-111111111111",
        "number": "0001",
        "title": "Тест",
        "status": "НеСогласована",
        "status_label": "Не согласована",
        "participants_count": 2,
        "participant_names": ["Иванов Иван Иванович", "Петров Петр Петрович"],
        "warnings": ["Нет времени"],
    }
    detail = build_detail_from_dashboard_item(item)
    assert detail["ref_key"] == item["ref_key"]
    assert detail["queue"]["number"] == "0001"
    assert detail["application"]["participants_count"] == 2
    assert detail["application"]["participants"][0]["full_name"] == "Иванов Иван Иванович"
    assert detail["warnings"] == ["Нет времени"]


def test_detail_is_agent_ready_rejects_dashboard_fallback() -> None:
    detail = build_detail_from_dashboard_item(
        {
            "ref_key": "111",
            "participant_names": ["Иванов И.И."],
            "participants_count": 1,
        }
    )
    assert detail_is_agent_ready(detail) is False


def test_detail_is_agent_ready_accepts_full_detail(agent_ready_detail) -> None:
    assert detail_is_agent_ready(agent_ready_detail) is True


@pytest.mark.asyncio
async def test_get_memo_detail_for_agent_returns_full_cache(agent_ready_detail) -> None:
    fetched_at = datetime(2026, 6, 18, 10, 0, tzinfo=timezone.utc)
    cached = {"payload": agent_ready_detail, "fetched_at": fetched_at}
    service = MeetingMemoCacheService()
    ref_key = "11111111-1111-1111-1111-111111111111"

    with patch.object(service, "_read_cache", AsyncMock(return_value=cached)):
        with patch.object(service, "_read_detail_from_dashboard_cache", AsyncMock()) as dashboard:
            payload, result_fetched_at, from_cache = await service.get_memo_detail_for_agent(ref_key)

    dashboard.assert_not_called()
    assert from_cache is True
    assert payload == agent_ready_detail
    assert result_fetched_at == fetched_at


@pytest.mark.asyncio
async def test_get_memo_detail_for_agent_fetches_from_onec_on_cache_miss(agent_ready_detail) -> None:
    fetched_at = datetime(2026, 6, 18, 11, 0, tzinfo=timezone.utc)
    service = MeetingMemoCacheService()
    ref_key = "11111111-1111-1111-1111-111111111111"

    with patch.object(service, "_read_cache", AsyncMock(return_value=None)):
        with patch.object(
            service,
            "_fetch_and_store",
            AsyncMock(return_value=(agent_ready_detail, fetched_at)),
        ) as fetch:
            with patch.object(service, "_read_detail_from_dashboard_cache", AsyncMock()) as dashboard:
                payload, result_fetched_at, from_cache = await service.get_memo_detail_for_agent(ref_key)

    fetch.assert_awaited_once_with(ref_key)
    dashboard.assert_not_called()
    assert from_cache is False
    assert payload == agent_ready_detail
    assert result_fetched_at == fetched_at


@pytest.mark.asyncio
async def test_get_memo_detail_for_agent_refetches_when_cache_is_dashboard_shaped(
    agent_ready_detail,
) -> None:
    fetched_at = datetime(2026, 6, 18, 10, 0, tzinfo=timezone.utc)
    dashboard_detail = build_detail_from_dashboard_item(
        {
            "ref_key": "11111111-1111-1111-1111-111111111111",
            "participant_names": ["Иванов И.И."],
            "participants_count": 1,
        }
    )
    cached = {"payload": dashboard_detail, "fetched_at": fetched_at}
    service = MeetingMemoCacheService()
    ref_key = "11111111-1111-1111-1111-111111111111"
    refetched_at = datetime(2026, 6, 18, 11, 0, tzinfo=timezone.utc)

    with patch.object(service, "_read_cache", AsyncMock(return_value=cached)):
        with patch.object(
            service,
            "_fetch_and_store",
            AsyncMock(return_value=(agent_ready_detail, refetched_at)),
        ) as fetch:
            payload, result_fetched_at, from_cache = await service.get_memo_detail_for_agent(ref_key)

    fetch.assert_awaited_once_with(ref_key)
    assert from_cache is False
    assert payload == agent_ready_detail
    assert result_fetched_at == refetched_at


@pytest.mark.asyncio
async def test_get_memo_detail_falls_back_to_dashboard_cache() -> None:
    fetched_at = datetime(2026, 6, 18, 10, 0, tzinfo=timezone.utc)
    item = {
        "ref_key": "11111111-1111-1111-1111-111111111111",
        "number": "0001",
        "title": "Тест",
        "status": "НеСогласована",
        "status_label": "Не согласована",
        "participants_count": 0,
        "warnings": [],
    }
    service = MeetingMemoCacheService()
    ref_key = "11111111-1111-1111-1111-111111111111"

    with patch.object(service, "_read_cache", AsyncMock(return_value=None)):
        with patch.object(
            service,
            "_read_detail_from_dashboard_cache",
            AsyncMock(return_value=(build_detail_from_dashboard_item(item), fetched_at)),
        ):
            payload, result_fetched_at, from_cache = await service.get_memo_detail(ref_key)

    assert from_cache is True
    assert payload["number"] == "0001"
    assert result_fetched_at == fetched_at
