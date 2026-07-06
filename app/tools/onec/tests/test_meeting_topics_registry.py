from datetime import date

from app.tools.onec.meeting_topics_registry import (
    build_filter_parts,
    is_topic_active,
    normalize_topic,
)


def test_is_topic_active_empty_close_date() -> None:
    assert is_topic_active("0001-01-01T00:00:00") is True
    assert is_topic_active(None) is True


def test_is_topic_active_future_close_date() -> None:
    assert is_topic_active("2026-12-31T00:00:00", today=date(2026, 7, 6)) is True


def test_is_topic_active_past_close_date() -> None:
    assert is_topic_active("2025-01-01T00:00:00", today=date(2026, 7, 6)) is False


def test_normalize_topic_marks_future_close_date_as_active() -> None:
    topic = normalize_topic(
        {
            "Ref_Key": "c7781365-f149-11f0-977f-6cb31113810c",
            "Code": "000009459",
            "Description": "Технический совет",
            "ВидСовещания": "Отчетное",
            "ДатаЗакрытияТемы": "2026-12-31T00:00:00",
        },
        expand_related=False,
    )

    assert topic["is_active"] is True
    assert topic["closed_date"] == "2026-12-31T00:00:00"


def test_build_filter_parts_active_only_includes_future_close_dates() -> None:
    parts = build_filter_parts(
        query=None,
        code=None,
        meeting_type=None,
        active_only=True,
        ref_key=None,
    )

    assert any("ДатаЗакрытияТемы ge datetime'" in part for part in parts)
