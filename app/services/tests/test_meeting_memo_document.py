"""Tests for memo text extraction."""
from __future__ import annotations

from app.services.meeting_memo_document import extract_memo_text


def test_extract_memo_text_ignores_agenda_and_theme() -> None:
    assert (
        extract_memo_text(
            {
                "ТемаСовещания": "тест периодичности",
                "ЦельПланаСовещания": "11",
            },
            application={"agenda": "тест периодичности"},
        )
        is None
    )


def test_extract_memo_text_reads_header_field() -> None:
    assert (
        extract_memo_text(
            {"ТекстСлужебнойЗаписки": "прошу распланировать ежедневные совещания на всю неделю"}
        )
        == "прошу распланировать ежедневные совещания на всю неделю"
    )
