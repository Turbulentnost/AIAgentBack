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
