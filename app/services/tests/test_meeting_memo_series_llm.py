from __future__ import annotations

import json
from datetime import date
from unittest.mock import AsyncMock, patch

import pytest

from app.models.enums import ScheduledMeetingFrequency, ScheduledMeetingWeekday
from app.services.meeting_memo_series_llm import (
    MemoSeriesLLMResponse,
    draft_from_llm_response,
    resolve_memo_recurrence_async,
)


def _header(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "Ref_Key": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        "ЖелаемаяДатаПроведенияСовещания": "2026-05-27T00:00:00",
        "ТекстСлужебнойЗаписки": "Прошу назначить совещание еженедельно по средам в 9:00",
    }
    base.update(overrides)
    return base


def test_draft_from_llm_weekly_response() -> None:
    response = MemoSeriesLLMResponse.model_validate(
        {
            "is_series": True,
            "confidence": "high",
            "frequency": "weekly",
            "interval": 1,
            "weekday": "wednesday",
            "time_local": "09:00",
            "duration_minutes": 60,
            "series_start_date": "2026-05-27",
            "series_end_date": "2026-12-31",
            "source_quote": "еженедельно по средам в 9:00",
            "ambiguities": [],
        }
    )
    draft = draft_from_llm_response(response, _header())

    assert draft.is_series is True
    assert draft.confidence == "high"
    assert draft.recurrence is not None
    assert draft.recurrence.frequency == ScheduledMeetingFrequency.WEEKLY
    assert draft.recurrence.weekday == ScheduledMeetingWeekday.WEDNESDAY
    assert draft.recurrence.series_end_date == date(2026, 12, 31)
    assert draft.planning_options == ["series", "single"]


def test_draft_from_llm_quarter_duration() -> None:
    response = MemoSeriesLLMResponse.model_validate(
        {
            "is_series": True,
            "confidence": "high",
            "frequency": "weekly",
            "interval": 1,
            "weekday": "wednesday",
            "time_local": "10:00",
            "duration_minutes": 60,
            "series_start_date": "2026-07-01",
            "series_end_date": "2026-09-30",
            "source_quote": "на весь квартал",
            "ambiguities": [],
            "reasoning": "Срок до конца Q3 2026",
        }
    )
    draft = draft_from_llm_response(
        response,
        _header(
            ТекстСлужебнойЗаписки="Прошу проводить совещание еженедельно по средам на весь квартал",
            ЖелаемаяДатаПроведенияСовещания="2026-07-01T00:00:00",
            ВремяНачалаСовещания="0001-01-01T10:00:00",
            ВремяОкончанияСовещания="0001-01-01T11:00:00",
        ),
    )

    assert draft.recurrence is not None
    assert draft.recurrence.series_end_date == date(2026, 9, 30)


def test_draft_from_llm_whole_week_code_counts_from_llm_dates() -> None:
    response = MemoSeriesLLMResponse.model_validate(
        {
            "is_series": True,
            "confidence": "high",
            "frequency": "daily",
            "interval": 1,
            "time_local": "12:00",
            "duration_minutes": 20,
            "series_start_date": "2026-07-20",
            "series_end_date": "2026-07-24",
            "source_quote": "прошу распланировать ежедневные совещания на всю неделю",
            "ambiguities": [],
        }
    )
    draft = draft_from_llm_response(
        response,
        _header(
            ТекстСлужебнойЗаписки="прошу распланировать ежедневные совещания на всю неделю",
            ЖелаемаяДатаПроведенияСовещания="2026-07-24T00:00:00",
            ВремяНачалаСовещания="0001-01-01T12:00:00",
            ВремяОкончанияСовещания="0001-01-01T12:20:00",
        ),
    )

    assert draft.recurrence is not None
    assert draft.planning_options == ["series", "single"]
    assert draft.occurrence_count == 5
    assert draft.recurrence_label == "ежедневно по будням, 12:00 · до 24.07.2026, 5 встреч"


def test_draft_from_llm_counts_meetings_from_llm_date_range_only() -> None:
    response = MemoSeriesLLMResponse.model_validate(
        {
            "is_series": True,
            "confidence": "high",
            "frequency": "daily",
            "interval": 1,
            "time_local": "12:00",
            "duration_minutes": 20,
            "series_start_date": "2026-07-24",
            "series_end_date": "2026-07-24",
            "source_quote": "прошу распланировать ежедневные совещания на всю неделю",
            "ambiguities": [],
        }
    )
    draft = draft_from_llm_response(
        response,
        _header(
            ТекстСлужебнойЗаписки="прошу распланировать ежедневные совещания на всю неделю",
            ЖелаемаяДатаПроведенияСовещания="2026-07-24T00:00:00",
            ВремяНачалаСовещания="0001-01-01T12:00:00",
            ВремяОкончанияСовещания="0001-01-01T12:20:00",
        ),
    )

    assert draft.occurrence_count == 1
    assert draft.recurrence_label == "ежедневно по будням, 12:00 · до 24.07.2026, 1 встреча"


@pytest.mark.asyncio
async def test_resolve_memo_recurrence_async_uses_llm_when_hints_present() -> None:
    llm_response = MemoSeriesLLMResponse.model_validate(
        {
            "is_series": True,
            "confidence": "high",
            "frequency": "weekly",
            "weekday": "wednesday",
            "time_local": "09:00",
            "duration_minutes": 60,
            "series_start_date": "2026-05-27",
            "series_end_date": "2026-12-31",
            "source_quote": "еженедельно по средам в 9:00",
            "ambiguities": [],
        }
    )

    async def fake_chat(*_args, **_kwargs):
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(llm_response.model_dump(mode="json")),
                    }
                }
            ]
        }

    with patch(
        "app.services.meeting_memo_series_llm._read_llm_cache",
        AsyncMock(return_value=None),
    ):
        with patch(
            "app.services.meeting_memo_series_llm._write_llm_cache",
            AsyncMock(),
        ):
            draft = await resolve_memo_recurrence_async(
                _header(),
                llm_chat=fake_chat,
                ref_key="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            )

    assert draft.is_series is True
    assert draft.planning_options == ["series", "single"]
    assert draft.recurrence is not None
    assert draft.recurrence.time_local.hour == 9


@pytest.mark.asyncio
async def test_resolve_memo_recurrence_async_falls_back_to_rules_on_llm_error() -> None:
    async def failing_chat(*_args, **_kwargs):
        raise RuntimeError("llm down")

    with patch(
        "app.services.meeting_memo_series_llm._read_llm_cache",
        AsyncMock(return_value=None),
    ):
        draft = await resolve_memo_recurrence_async(
            _header(),
            llm_chat=failing_chat,
        )

    assert draft.is_series is True
    assert draft.recurrence is not None
    assert draft.recurrence.frequency == ScheduledMeetingFrequency.WEEKLY


def test_extract_assistant_text_prefers_json_in_reasoning() -> None:
    from app.services.meeting_memo_series_llm import _extract_assistant_text

    message = {
        "content": "Сначала подумаю без JSON.",
        "reasoning_content": (
            '<think>анализ</think>\n'
            '{"is_series": true, "frequency": "weekly", "confidence": "high"}'
        ),
    }

    text = _extract_assistant_text(message)
    assert '"is_series": true' in text
    assert "<think>" not in text


@pytest.mark.asyncio
async def test_call_memo_series_llm_retries_when_first_answer_not_json() -> None:
    from app.services.meeting_memo_series_llm import call_memo_series_llm

    good = {
        "is_series": True,
        "confidence": "high",
        "frequency": "weekly",
        "interval": 1,
        "weekday": "wednesday",
        "time_local": "09:00",
        "duration_minutes": 60,
        "series_start_date": "2026-05-27",
        "series_end_date": "2026-12-31",
        "source_quote": "еженедельно",
        "ambiguities": [],
    }
    calls = {"n": 0}

    async def flaky_chat(messages, **_kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"choices": [{"message": {"content": "не JSON, а рассуждение"}}]}
        return {"choices": [{"message": {"content": json.dumps(good, ensure_ascii=False)}}]}

    response = await call_memo_series_llm(_header(), None, llm_chat=flaky_chat)
    assert response.is_series is True
    assert response.frequency == "weekly"
    assert calls["n"] == 2
