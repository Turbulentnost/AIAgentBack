from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app.services.meeting_memo_cache import (
    MeetingMemoCacheService,
    MemoCacheMissError,
    _apply_dashboard_item_to_cached_detail,
    _cache_key,
    build_detail_from_dashboard_item,
    collect_memo_ref_keys,
    detail_is_agent_ready,
    ensure_memo_text_in_detail,
    refresh_cached_detail_assessment,
    warm_memo_details_from_dashboard,
)


@pytest.fixture
def sample_detail() -> dict:
    return {
        "ref_key": "11111111-1111-1111-1111-111111111111",
        "number": "000008622",
        "title": "Прошу назначить совещание",
        "queue": {"ref_key": "11111111-1111-1111-1111-111111111111"},
        "application": {
            "participants": [],
            "manager": {"full_name": "Иванов И.И."},
        },
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


def test_refresh_cached_detail_assessment_accepts_manager_from_application() -> None:
    detail = {
        "ref_key": "11111111-1111-1111-1111-111111111111",
        "queue": {"ТемаСлужебнойЗаписки": "Организация совещаний (регл.)"},
        "application": {
            "manager": {"full_name": "Самарская Е.В."},
            "participants": [{"full_name": "Иванов И.И."}],
        },
    }
    refreshed = refresh_cached_detail_assessment(detail)
    manager_item = next(
        item for item in refreshed["sto_checklist"] if item["field"] == "meeting_manager"
    )
    assert manager_item["passed"] is True


def test_refresh_cached_detail_assessment_maps_location_and_theme() -> None:
    detail = {
        "ref_key": "11111111-1111-1111-1111-111111111111",
        "queue": {
            "ТемаСлужебнойЗаписки": "Организация совещаний (регл.)",
            "location": "Зал совещаний КБ",
            "subject": "Тестовая тема совещания",
            "desired_meeting_date": "2026-07-10T00:00:00",
            "document_date": "2026-07-08T09:49:00",
        },
        "application": {
            "manager": {"full_name": "Самарская Е.В."},
            "meeting_start": "2026-07-10T13:00:00",
            "meeting_end": "2026-07-10T14:00:00",
            "participants": [{"full_name": "Иванов И.И."}],
            "participants_count": 1,
        },
    }
    refreshed = refresh_cached_detail_assessment(detail)
    location_item = next(item for item in refreshed["sto_checklist"] if item["field"] == "location")
    theme_item = next(item for item in refreshed["sto_checklist"] if item["field"] == "meeting_theme")
    desired_item = next(
        item for item in refreshed["sto_checklist"] if item["field"] == "desired_meeting_date"
    )
    assert location_item["passed"] is True
    assert theme_item["passed"] is True
    assert desired_item["message"] == "10.07.2026"
    assert refreshed["document_date_label"] == "08.07.2026"
    assert refreshed["application"]["document_date_label"] == "08.07.2026"
    assert refreshed["queue"]["document_date_label"] == "08.07.2026"


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
    assert payload["ref_key"] == sample_detail["ref_key"]
    assert isinstance(payload.get("sto_checklist"), list)
    assert len(payload["sto_checklist"]) > 0
    assert result_fetched_at == fetched_at


@pytest.mark.asyncio
async def test_get_memo_detail_raises_on_cache_miss(sample_detail) -> None:
    service = MeetingMemoCacheService()
    ref_key = "11111111-1111-1111-1111-111111111111"

    with patch.object(service, "_read_cache", AsyncMock(return_value=None)):
        with patch.object(service, "_read_detail_from_dashboard_cache", AsyncMock(return_value=None)):
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
            with patch(
                "app.services.meeting_memo_cache.enrich_memo_detail_payload",
                AsyncMock(side_effect=lambda payload, **_: payload),
            ):
                with patch.object(
                    service,
                    "_apply_cached_series_choice",
                    AsyncMock(side_effect=lambda payload, _: payload),
                ):
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


def test_build_detail_from_dashboard_item_includes_memo_text() -> None:
    item = {
        "ref_key": "11111111-1111-1111-1111-111111111111",
        "number": "0001",
        "title": "Тест периодичности",
        "ТекстСлужебнойЗаписки": "Прошу назначить совещание еженедельно по средам в 9:00",
    }
    detail = build_detail_from_dashboard_item(item)
    assert detail["application"]["memo_text"] == item["ТекстСлужебнойЗаписки"]


def test_refresh_cached_detail_assessment_does_not_use_agenda_as_memo_text() -> None:
    detail = build_detail_from_dashboard_item(
        {
            "ref_key": "11111111-1111-1111-1111-111111111111",
            "title": "тест периодичности",
            "ТемаСовещания": "тест периодичности",
            "subject": "тест периодичности",
        }
    )
    refreshed = refresh_cached_detail_assessment(detail, include_series_planning=False)
    assert refreshed["application"].get("memo_text") is None
    assert refreshed["application"]["agenda"] == "тест периодичности"


@pytest.mark.asyncio
async def test_ensure_memo_text_in_detail_fetches_from_onec() -> None:
    detail = build_detail_from_dashboard_item(
        {
            "ref_key": "ffc53a2a-866d-11f1-984a-6cb31113810e",
            "title": "тест периодичности",
            "ТемаСовещания": "тест периодичности",
        }
    )
    with (
        patch(
            "app.services.meeting_memo_cache.fetch_document_header",
            return_value={
                "Ref_Key": "ffc53a2a-866d-11f1-984a-6cb31113810e",
                "ТекстСлужебнойЗаписки": "прошу распланировать ежедневные совещания на всю неделю",
            },
        ),
        patch(
            "app.services.meeting_memo_cache._persist_memo_detail_cache",
            AsyncMock(),
        ) as persist,
    ):
        updated = await ensure_memo_text_in_detail(
            detail,
            ref_key="ffc53a2a-866d-11f1-984a-6cb31113810e",
        )
    assert (
        updated["application"]["memo_text"]
        == "прошу распланировать ежедневные совещания на всю неделю"
    )
    persist.assert_awaited_once()


@pytest.mark.asyncio
async def test_ensure_memo_text_marks_unavailable_on_access_denied() -> None:
    detail = build_detail_from_dashboard_item(
        {
            "ref_key": "ffc53a2a-866d-11f1-984a-6cb31113810e",
            "title": "тест",
        }
    )
    with (
        patch(
            "app.services.meeting_memo_cache.fetch_document_header",
            side_effect=RuntimeError("HTTP 500: Нарушение прав доступа!"),
        ) as fetch_header,
        patch(
            "app.services.meeting_memo_cache._persist_memo_detail_cache",
            AsyncMock(),
        ) as persist,
    ):
        updated = await ensure_memo_text_in_detail(
            detail,
            ref_key="ffc53a2a-866d-11f1-984a-6cb31113810e",
        )
        again = await ensure_memo_text_in_detail(
            updated,
            ref_key="ffc53a2a-866d-11f1-984a-6cb31113810e",
        )

    assert updated["application"]["memo_text_unavailable"] is True
    assert again["application"]["memo_text_unavailable"] is True
    fetch_header.assert_called_once()
    persist.assert_awaited_once()


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
    assert payload["ref_key"] == agent_ready_detail["ref_key"]
    assert isinstance(payload.get("sto_checklist"), list)
    assert len(payload["sto_checklist"]) > 0
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
                with patch(
                    "app.services.meeting_memo_cache.enrich_memo_detail_payload",
                    AsyncMock(side_effect=lambda payload, **_: payload),
                ):
                    with patch.object(
                        service,
                        "_apply_cached_series_choice",
                        AsyncMock(side_effect=lambda payload, _: payload),
                    ):
                        payload, result_fetched_at, from_cache = await service.get_memo_detail_for_agent(
                            ref_key
                        )

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
            with patch(
                "app.services.meeting_memo_cache.enrich_memo_detail_payload",
                AsyncMock(side_effect=lambda payload, **_: payload),
            ):
                with patch.object(
                    service,
                    "_apply_cached_series_choice",
                    AsyncMock(side_effect=lambda payload, _: payload),
                ):
                    payload, result_fetched_at, from_cache = await service.get_memo_detail_for_agent(
                        ref_key
                    )

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
    assert isinstance(payload.get("sto_checklist"), list)
    assert result_fetched_at == fetched_at


def test_apply_dashboard_item_updates_stale_participants() -> None:
    existing = {
        "ref_key": "memo-1",
        "number": "000011991",
        "title": "ДПИ ИИ",
        "queue": {
            "participant_names": [
                "Никитаев Алексей Вячеславович",
                "Ясыров Богдан Джумазаевич",
            ],
            "ТекстСлужебнойЗаписки": "старый текст",
        },
        "application": {
            "memo_text": "старый текст",
            "participants": [
                {"full_name": "Никитаев Алексей Вячеславович", "ref_key": None},
                {"full_name": "Ясыров Богдан Джумазаевич", "ref_key": None},
            ],
            "participants_count": 2,
            "manager": {"full_name": "Соломичева Светлана Викторовна"},
        },
        "validation_checks": [{"field": "keep"}],
    }
    item = {
        "ref_key": "memo-1",
        "number": "000011991",
        "title": "ДПИ ИИ",
        "subject": "ДПИ ИИ",
        "participant_names": [
            "Ясыров Богдан Джумазаевич",
            "Лапина Арина Антоновна",
        ],
        "participants_count": 2,
        "initiator": {"full_name": "Комарькова Анастасия Эдуардовна"},
        "manager": {"full_name": "Соломичева Светлана Викторовна"},
        "ТекстСлужебнойЗаписки": "старый текст",
    }

    patched = _apply_dashboard_item_to_cached_detail(existing, item)
    names = [p["full_name"] for p in patched["application"]["participants"]]

    assert names == [
        "Ясыров Богдан Джумазаевич",
        "Лапина Арина Антоновна",
    ]
    assert patched["queue"]["participant_names"] == [
        "Ясыров Богдан Джумазаевич",
        "Лапина Арина Антоновна",
    ]
    assert patched["validation_checks"] == [{"field": "keep"}]
    assert patched["application"]["memo_text"] == "старый текст"


@pytest.mark.asyncio
async def test_warm_memo_details_overwrites_participants_on_existing_cache() -> None:
    existing = {
        "ref_key": "memo-1",
        "application": {
            "memo_text": "текст есть",
            "participants": [{"full_name": "Старый Участник"}],
            "participants_count": 1,
        },
        "queue": {"participant_names": ["Старый Участник"]},
    }
    item = {
        "ref_key": "memo-1",
        "participant_names": ["Новый Участник"],
        "participants_count": 1,
        "ТекстСлужебнойЗаписки": "текст есть",
        "initiator": {"full_name": "Инициатор"},
        "manager": {"full_name": "Руководитель"},
    }
    write_mock = AsyncMock()
    service = MeetingMemoCacheService()

    with (
        patch(
            "app.services.meeting_memo_cache.settings.MEETING_DASHBOARD_CACHE_ENABLED",
            True,
        ),
        patch(
            "app.services.meeting_memo_cache.MeetingMemoCacheService",
            return_value=service,
        ),
        patch.object(
            service,
            "_read_cache",
            AsyncMock(return_value={"payload": existing, "fetched_at": "old"}),
        ),
        patch.object(service, "_write_cache", write_mock),
    ):
        await warm_memo_details_from_dashboard({"items": [item]})

    write_mock.assert_awaited_once()
    written = write_mock.await_args.args[1]
    names = [p["full_name"] for p in written["application"]["participants"]]
    assert names == ["Новый Участник"]
