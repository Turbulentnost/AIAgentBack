from datetime import date
from unittest.mock import MagicMock, patch

from app.tools.onec.meeting_topics_registry import (
    build_filter_parts,
    fetch_topic_by_key,
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


def test_is_topic_active_today_close_date_is_inactive() -> None:
    assert is_topic_active("2026-07-22T00:00:00", today=date(2026, 7, 22)) is False


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

    assert any("ДатаЗакрытияТемы gt datetime'" in part for part in parts)


def test_fetch_topic_by_key_falls_back_when_expand_unsupported() -> None:
    session = MagicMock()
    config = MagicMock()
    config.url = "http://example/odata"
    config.timeout = 30
    raw_row = {
        "Ref_Key": "8296c4b9-3e91-49f8-b957-2571d9aacec7",
        "Code": "000010399",
        "Description": "тест",
        "DeletionMark": False,
    }

    with patch(
        "app.tools.onec.meeting_topics_registry.odata_get_json",
        side_effect=[RuntimeError("501"), raw_row],
    ) as get_json:
        row = fetch_topic_by_key(
            session,
            config,
            "8296c4b9-3e91-49f8-b957-2571d9aacec7",
            expand_related=True,
        )

    assert row == raw_row
    assert get_json.call_count == 2
    assert "$expand=" not in get_json.call_args_list[1].args[1]
