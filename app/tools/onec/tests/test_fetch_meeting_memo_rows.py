from unittest.mock import MagicMock, patch

import pytest

from app.tools.onec.get_meetings import fetch_meeting_memo_rows


def test_fetch_meeting_memo_rows_raises_when_all_queries_fail() -> None:
    session = MagicMock()
    config = MagicMock()
    metadata = MagicMock()

    with patch(
        "app.tools.onec.get_meetings.fetch_documents_by_filter",
        side_effect=RuntimeError("HTTP 401"),
    ):
        with pytest.raises(RuntimeError, match="HTTP 401"):
            fetch_meeting_memo_rows(
                session,
                config,
                "Статус eq 'НеСогласована'",
                limit=10,
                fetch_pool=10,
                metadata=metadata,
            )


def test_fetch_meeting_memo_rows_merges_guid_themed_rows_when_theme_key_resolved() -> None:
    session = MagicMock()
    config = MagicMock()
    metadata = MagicMock()
    theme_key = "cad8df76-73cc-11ea-8341-ac1f6b05524d"
    text_row = {
        "Ref_Key": "abc",
        "ТемаСлужебнойЗаписки": "Организация совещаний (регл.)",
        "Number": "000000001",
    }
    guid_row = {
        "Ref_Key": "37da8ed8-6b19-11f1-9825-6cb31113810e",
        "ТемаСлужебнойЗаписки": theme_key,
        "Number": "000009938",
        "Date": "2026-06-18T16:25:57",
    }

    def fetch_side_effect(_session, _config, odata_filter, **_kwargs):
        if "ТемаСлужебнойЗаписки eq" in odata_filter:
            return [text_row]
        return [guid_row]

    with patch(
        "app.tools.onec.get_meetings.fetch_documents_by_filter",
        side_effect=fetch_side_effect,
    ) as fetch_documents:
        with patch(
            "app.tools.onec.get_meetings.resolve_theme_key",
            return_value=theme_key,
        ):
            rows = fetch_meeting_memo_rows(
                session,
                config,
                "Статус eq 'НеСогласована'",
                limit=10,
                fetch_pool=10,
                metadata=metadata,
            )

    assert len(rows) == 2
    assert {row["Number"] for row in rows} == {"000009938", "000000001"}
    assert fetch_documents.call_count == 2
    text_filter = fetch_documents.call_args_list[0].args[2]
    assert "ТемаСлужебнойЗаписки eq 'Организация совещаний (регл.)'" in text_filter


def test_fetch_meeting_memo_rows_skips_broad_when_text_rows_found() -> None:
    session = MagicMock()
    config = MagicMock()
    metadata = MagicMock()
    text_row = {"Ref_Key": "abc", "ТемаСлужебнойЗаписки": "Организация совещаний (регл.)"}

    with patch(
        "app.tools.onec.get_meetings.fetch_documents_by_filter",
        return_value=[text_row],
    ) as fetch_documents:
        with patch(
            "app.tools.onec.get_meetings.resolve_theme_key",
            return_value=None,
        ):
            rows = fetch_meeting_memo_rows(
                session,
                config,
                "Статус eq 'НеСогласована'",
                limit=10,
                fetch_pool=10,
                metadata=metadata,
            )

    assert rows == [text_row]
    assert fetch_documents.call_count == 1


def test_fetch_meeting_memo_rows_returns_empty_when_queries_succeed_without_rows() -> None:
    session = MagicMock()
    config = MagicMock()
    metadata = MagicMock()

    with patch(
        "app.tools.onec.get_meetings.fetch_documents_by_filter",
        return_value=[],
    ):
        rows = fetch_meeting_memo_rows(
            session,
            config,
            "Статус eq 'НеСогласована'",
            limit=10,
            fetch_pool=10,
            metadata=metadata,
        )

    assert rows == []
