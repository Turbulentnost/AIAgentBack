"""Тесты on-demand загрузки тела письма из IMAP."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from agent_pochta.api.app import app
from agent_pochta.db.models import EmailMessageRow
from agent_pochta.db.repository import EmailRepository
from agent_pochta.email_payload import BODY_NOT_STORED_PLACEHOLDER
from agent_pochta.imap.body_fetch import (
    fetch_and_cache_email_body,
    row_has_cached_body,
)
from agent_pochta.imap.client import imap_header_message_id


def test_imap_header_message_id_normalizes():
    assert imap_header_message_id("abc@example.com") == "<abc@example.com>"
    assert imap_header_message_id("<abc@example.com>") == "<abc@example.com>"
    assert imap_header_message_id("<abc@example.com>#jurist@x.ru") == "<abc@example.com>"


def test_row_has_cached_body():
    row = EmailMessageRow(
        id=uuid.uuid4(),
        message_id="<cached@example>",
        received_at=datetime.now(timezone.utc).replace(tzinfo=None),
        mailbox="info@turbo-don.ru",
        sender_email="a@b.ru",
        raw_payload_json=json.dumps({"body_text": "Cached text"}, ensure_ascii=False),
    )
    assert row_has_cached_body(row) is True

    empty_row = EmailMessageRow(
        id=uuid.uuid4(),
        message_id="<empty@example>",
        received_at=datetime.now(timezone.utc).replace(tzinfo=None),
        mailbox="info@turbo-don.ru",
        sender_email="a@b.ru",
        raw_payload_json=json.dumps({"body_text": ""}, ensure_ascii=False),
    )
    assert row_has_cached_body(empty_row) is False


@patch("agent_pochta.imap.body_fetch.fetch_message_from_imap")
def test_fetch_and_cache_email_body_stores_text(mock_fetch):
    row_id = uuid.uuid4()
    mock_fetch.return_value = ("Текст из IMAP", None)

    session = MagicMock()
    row = EmailMessageRow(
        id=row_id,
        message_id="<imap@example>",
        received_at=datetime.now(timezone.utc).replace(tzinfo=None),
        mailbox="info@turbo-don.ru",
        sender_email="a@b.ru",
        raw_payload_json=json.dumps(
            {"message_id": "<imap@example>", "body_text": ""},
            ensure_ascii=False,
        ),
    )
    repo = MagicMock(spec=EmailRepository)
    repo.get_by_id.return_value = row
    repo.cache_fetched_body.return_value = row

    with patch("agent_pochta.imap.body_fetch.get_session_factory") as mock_factory:
        mock_factory.return_value.return_value.__enter__.return_value = session
        with patch("agent_pochta.imap.body_fetch.EmailRepository", return_value=repo):
            result = fetch_and_cache_email_body(row_id, vault=MagicMock())

    assert result.ok is True
    assert result.body_text == "Текст из IMAP"
    assert result.cached is False
    repo.cache_fetched_body.assert_called_once_with(
        row_id,
        body_text="Текст из IMAP",
        body_html=None,
    )
    session.commit.assert_called_once()
    mock_fetch.assert_called_once()


@patch("agent_pochta.api.app.fetch_and_cache_email_body")
def test_fetch_body_endpoint_returns_cached_without_task(mock_fetch):
    row_id = uuid.uuid4()
    row = EmailMessageRow(
        id=row_id,
        message_id="<cached@example>",
        received_at=datetime.now(timezone.utc).replace(tzinfo=None),
        mailbox="info@turbo-don.ru",
        sender_email="a@b.ru",
        raw_payload_json=json.dumps({"body_text": "Уже в кеше"}, ensure_ascii=False),
    )
    repo = MagicMock(spec=EmailRepository)
    repo.get_by_id.return_value = row

    with patch("agent_pochta.api.app.get_session_factory") as mock_factory:
        mock_factory.return_value.return_value.__enter__.return_value = MagicMock()
        with patch("agent_pochta.api.app.EmailRepository", return_value=repo):
            client = TestClient(app)
            response = client.post(f"/api/v1/email-messages/{row_id}/fetch-body")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ready"
    assert data["body_text"] == "Уже в кеше"
    assert data["cached"] is True
    mock_fetch.assert_not_called()


@patch("agent_pochta.api.app.fetch_and_cache_email_body")
def test_fetch_body_endpoint_fetches_from_imap(mock_fetch):
    row_id = uuid.uuid4()
    row = EmailMessageRow(
        id=row_id,
        message_id="<missing@example>",
        received_at=datetime.now(timezone.utc).replace(tzinfo=None),
        mailbox="info@turbo-don.ru",
        sender_email="a@b.ru",
        raw_payload_json=json.dumps({"body_text": ""}, ensure_ascii=False),
    )
    repo = MagicMock(spec=EmailRepository)
    repo.get_by_id.return_value = row

    from agent_pochta.imap.body_fetch import FetchEmailBodyResult

    mock_fetch.return_value = FetchEmailBodyResult(
        ok=True,
        row_id=str(row_id),
        body_text="Загружено из IMAP",
        cached=False,
    )

    with patch("agent_pochta.api.app.get_session_factory") as mock_factory:
        mock_factory.return_value.return_value.__enter__.return_value = MagicMock()
        with patch("agent_pochta.api.app.EmailRepository", return_value=repo):
            client = TestClient(app)
            response = client.post(f"/api/v1/email-messages/{row_id}/fetch-body")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ready"
    assert data["body_text"] == "Загружено из IMAP"
    mock_fetch.assert_called_once_with(row_id)


@patch("agent_pochta.api.app.fetch_and_cache_email_body")
def test_fetch_body_endpoint_not_in_mailbox(mock_fetch):
    row_id = uuid.uuid4()
    row = EmailMessageRow(
        id=row_id,
        message_id="<gone@example>",
        received_at=datetime.now(timezone.utc).replace(tzinfo=None),
        mailbox="info@turbo-don.ru",
        sender_email="a@b.ru",
        raw_payload_json=json.dumps({"body_text": ""}, ensure_ascii=False),
    )
    repo = MagicMock(spec=EmailRepository)
    repo.get_by_id.return_value = row

    from agent_pochta.imap.body_fetch import FetchEmailBodyResult

    mock_fetch.return_value = FetchEmailBodyResult(
        ok=False,
        row_id=str(row_id),
        reason="not_in_mailbox",
    )

    with patch("agent_pochta.api.app.get_session_factory") as mock_factory:
        mock_factory.return_value.return_value.__enter__.return_value = MagicMock()
        with patch("agent_pochta.api.app.EmailRepository", return_value=repo):
            client = TestClient(app)
            response = client.post(f"/api/v1/email-messages/{row_id}/fetch-body")

    assert response.status_code == 404
    assert "не найдено" in response.json()["detail"].lower()


def test_row_to_dict_after_cache_shows_body():
    from agent_pochta.api.app import _row_to_dict

    row = EmailMessageRow(
        id=uuid.uuid4(),
        message_id="<cached@example>",
        received_at=datetime.now(timezone.utc).replace(tzinfo=None),
        mailbox="info@turbo-don.ru",
        sender_email="a@b.ru",
        raw_payload_json=json.dumps(
            {
                "body_text": "Текст после fetch-body",
                "body_fetched_at": "2026-07-03T08:00:00Z",
            },
            ensure_ascii=False,
        ),
    )
    data = _row_to_dict(row)
    assert data["body_text"] == "Текст после fetch-body"
    assert data["body_text"] != BODY_NOT_STORED_PLACEHOLDER
