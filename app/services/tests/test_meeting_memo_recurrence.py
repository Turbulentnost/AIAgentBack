from __future__ import annotations

from datetime import date

from app.models.enums import ScheduledMeetingFrequency, ScheduledMeetingWeekday
from app.services.meeting_memo_recurrence import (
    has_recurrence_hints,
    resolve_memo_recurrence,
    resolve_memo_recurrence_rules,
)


def _header(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "ЖелаемаяДатаПроведенияСовещания": "2026-07-24T00:00:00",
        "ВремяНачалаСовещания": "0001-01-01T13:00:00",
        "ВремяОкончанияСовещания": "0001-01-01T13:20:00",
    }
    base.update(overrides)
    return base


def test_daily_two_weeks_with_time_in_text() -> None:
    header = _header(
        ТекстСлужебнойЗаписки=(
            "Прошу распланировать совещания по этой теме на две недели "
            "ежедневно с 13:15-14:00"
        ),
    )
    draft = resolve_memo_recurrence_rules(header)

    assert draft.is_series is True
    assert draft.confidence == "high"
    assert draft.recurrence is not None
    assert draft.recurrence.frequency == ScheduledMeetingFrequency.DAILY
    assert draft.recurrence.interval == 1
    assert draft.recurrence.series_start_date == date(2026, 7, 24)
    assert draft.recurrence.series_end_date == date(2026, 8, 6)
    # 24.07–06.08.2026 без выходных = 10 рабочих дней
    assert draft.occurrence_count == 10
    assert draft.requires_user_choice is True
    assert draft.planning_options == ["series", "single"]
    assert draft.recurrence_label is not None
    assert "ежедневно" in draft.recurrence_label
    assert "10" in draft.recurrence_label


def test_sync_resolve_returns_stub_for_text_hints() -> None:
    header = _header(ТекстСлужебнойЗаписки="еженедельно по средам в 9:00")
    draft = resolve_memo_recurrence(header)

    assert draft.is_series is True
    assert draft.planning_options == ["single"]
    assert has_recurrence_hints([header["ТекстСлужебнойЗаписки"]])


def test_daily_without_end_defaults_to_year_end() -> None:
    header = _header(ТекстСлужебнойЗаписки="Прошу проводить совещание ежедневно")
    draft = resolve_memo_recurrence_rules(header)

    assert draft.is_series is True
    assert draft.confidence == "high"
    assert draft.recurrence is not None
    assert draft.recurrence.series_end_date == date(2026, 12, 31)
    assert draft.requires_user_choice is True
    assert draft.planning_options == ["series", "single"]
    assert not any("окончания" in item for item in draft.ambiguities)


def test_weekly_at_time_without_duration_defaults_to_sixty_minutes() -> None:
    header = _header(
        ТекстСлужебнойЗаписки="Прошу назначить совещание еженедельно по средам в 9:00",
    )
    draft = resolve_memo_recurrence_rules(header)

    assert draft.is_series is True
    assert draft.confidence == "high"
    assert draft.recurrence is not None
    assert draft.recurrence.time_local.hour == 9
    assert draft.recurrence.duration_minutes == 60
    assert draft.recurrence.series_end_date == date(2026, 12, 31)
    assert draft.planning_options == ["series", "single"]


def test_period_in_text_time_in_header() -> None:
    header = _header(
        ТекстСлужебнойЗаписки="Прошу распланировать совещания на две недели ежедневно",
    )
    draft = resolve_memo_recurrence_rules(header)

    assert draft.is_series is True
    assert draft.confidence == "high"
    assert draft.recurrence is not None
    assert draft.recurrence.duration_minutes == 20
    assert draft.recurrence.time_local.hour == 13
    assert draft.recurrence.time_local.minute == 0


def test_no_recurrence_hints() -> None:
    header = _header(ТекстСлужебнойЗаписки="Прошу организовать совещание по проекту")
    draft = resolve_memo_recurrence(header)

    assert draft.is_series is False
    assert draft.confidence == "high"
    assert draft.recurrence is None
    assert draft.planning_options == ["single"]


def test_biweekly_on_tuesday() -> None:
    header = _header(
        ЖелаемаяДатаПроведенияСовещания="2026-07-21T00:00:00",
        ТекстСлужебнойЗаписки=(
            "Прошу проводить совещание раз в две недели по вторникам до 20.08.2026"
        ),
    )
    draft = resolve_memo_recurrence_rules(header)

    assert draft.is_series is True
    assert draft.confidence == "high"
    assert draft.recurrence is not None
    assert draft.recurrence.frequency == ScheduledMeetingFrequency.WEEKLY
    assert draft.recurrence.interval == 2
    assert draft.recurrence.weekday == ScheduledMeetingWeekday.TUESDAY
    assert draft.recurrence.series_end_date == date(2026, 8, 20)


def test_daily_without_desired_date_is_low_confidence() -> None:
    header = {
        "ТекстСлужебнойЗаписки": "Прошу распланировать совещания на две недели ежедневно",
        "ВремяНачалаСовещания": "0001-01-01T10:00:00",
        "ВремяОкончанияСовещания": "0001-01-01T11:00:00",
    }
    draft = resolve_memo_recurrence_rules(header)

    assert draft.is_series is True
    assert draft.confidence == "low"
    assert draft.recurrence is None
    assert any("желаемая дата" in item.lower() for item in draft.ambiguities)


def test_weekdays_only_is_not_supported() -> None:
    header = _header(
        ТекстСлужебнойЗаписки="Прошу проводить совещание по будням на две недели",
    )
    draft = resolve_memo_recurrence_rules(header)

    assert draft.is_series is True
    assert draft.confidence == "low"
    assert draft.recurrence is None
    assert any("будням" in item for item in draft.ambiguities)


def test_resolve_source_quote_ignores_theme_guid_and_other_fields() -> None:
    header = _header(
        ТекстСлужебнойЗаписки="прошу распланировать ежедневные совещания на всю неделю",
        ТемаСлужебнойЗаписки="cad8df76-73cc-11ea-8341-ac1f6b05524d",
        ТемаСовещания="тест периодичности",
        ЦельПланаСовещания="11",
    )
    draft = resolve_memo_recurrence_rules(header)

    assert draft.source_quote == "прошу распланировать ежедневные совещания на всю неделю"


def test_rules_defer_whole_week_without_llm() -> None:
    header = _header(
        ТекстСлужебнойЗаписки="прошу распланировать ежедневные совещания на всю неделю",
        ЖелаемаяДатаПроведенияСовещания="2026-07-24T00:00:00",
        ВремяНачалаСовещания="0001-01-01T12:00:00",
        ВремяОкончанияСовещания="0001-01-01T12:20:00",
    )
    draft = resolve_memo_recurrence_rules(header)

    assert draft.recurrence is None
    assert draft.confidence == "low"
    assert any("LLM" in item for item in draft.ambiguities)


def test_build_memo_series_messages_prefills_no_think_for_qwen() -> None:
    from app.services.meeting_memo_series_llm import _build_memo_series_messages

    messages = _build_memo_series_messages({}, None, model="qwen/qwen3.5-9b")
    assert messages[-1]["role"] == "assistant"
    assert messages[-1]["content"].startswith("/no_think")


def test_build_memo_series_messages_prefills_no_think_for_gpt_oss() -> None:
    from app.services.meeting_memo_series_llm import _build_memo_series_messages

    messages = _build_memo_series_messages({}, None, model="openai/gpt-oss-120b")
    assert messages[-1]["content"].startswith("/no_think")
