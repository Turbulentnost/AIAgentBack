"""Тесты фильтров списка писем и пагинации API."""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from agent_pochta.api.app import app
from agent_pochta.db.message_filters import (
    msk_day_end_exclusive_utc,
    msk_day_start_utc,
    parse_optional_date,
)
from agent_pochta.db.models import EmailMessageRow


def test_parse_optional_date():
    assert parse_optional_date(None) is None
    assert parse_optional_date("  ") is None
    assert parse_optional_date("2026-07-02") == date(2026, 7, 2)


def test_msk_day_bounds():
    start = msk_day_start_utc(date(2026, 7, 2))
    end = msk_day_end_exclusive_utc(date(2026, 7, 2))
    assert start == datetime(2026, 7, 1, 21, 0, 0)
    assert end == datetime(2026, 7, 2, 21, 0, 0)


def _email_row(*, status: str = "done") -> EmailMessageRow:
    received_at = datetime.now(timezone.utc).replace(tzinfo=None)
    return EmailMessageRow(
        id=uuid.uuid4(),
        message_id="<list@example>",
        received_at=received_at,
        mailbox="info@turbo-don.ru",
        sender_email="vendor@example.com",
        sender_name="Vendor",
        subject="Акт сверки",
        status=status,
    )


@contextmanager
def _mock_repo(*, rows: list[EmailMessageRow], total: int, by_status: dict[str, int] | None = None):
    repo = MagicMock()
    repo.list_messages.return_value = rows
    repo.count_messages.return_value = total
    repo.count_by_status.return_value = by_status or {row.status: 1 for row in rows}
    session = MagicMock()
    session.__enter__.return_value = session
    session.__exit__.return_value = False

    with patch("agent_pochta.api.app.get_session_factory", return_value=lambda: session):
        with patch("agent_pochta.api.app.EmailRepository", return_value=repo):
            yield repo


def test_list_email_messages_returns_page():
    row = _email_row()
    client = TestClient(app)
    with _mock_repo(rows=[row], total=1):
        response = client.get("/api/v1/email-messages", params={"limit": 50, "offset": 0})
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert payload["limit"] == 50
    assert payload["offset"] == 0
    assert len(payload["items"]) == 1
    assert payload["items"][0]["id"] == str(row.id)
    assert "body_text" not in payload["items"][0]


def test_email_messages_stats_endpoint():
    client = TestClient(app)
    with _mock_repo(rows=[], total=3, by_status={"done": 2, "spam": 1}):
        response = client.get("/api/v1/email-messages/stats")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 3
    assert payload["by_status"]["done"] == 2
    assert payload["by_status"]["spam"] == 1


def test_list_email_messages_rejects_invalid_date_range():
    client = TestClient(app)
    response = client.get(
        "/api/v1/email-messages",
        params={"date_from": "2026-07-10", "date_to": "2026-07-01"},
    )
    assert response.status_code == 400
