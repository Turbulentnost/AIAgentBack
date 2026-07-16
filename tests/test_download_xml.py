"""Тесты on-demand скачивания XML-документа из БД (без записи на диск)."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from agent_pochta.api.app import _xml_download_filename, app
from agent_pochta.db.models import EmailMessageRow
from agent_pochta.db.repository import EmailRepository

SAMPLE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<document>
  <organization>НП</organization>
  <theme>Тест</theme>
</document>
"""


def test_xml_download_filename():
    row_id = uuid.UUID("12345678-1234-5678-1234-567812345678")
    assert _xml_download_filename(row_id) == "incoming_12345678.xml"


def test_download_xml_endpoint_streams_from_db():
    row_id = uuid.uuid4()
    row = EmailMessageRow(
        id=row_id,
        message_id="<xml-download@mail.ru>",
        received_at=datetime.now(timezone.utc).replace(tzinfo=None),
        mailbox="info@turbo-don.ru",
        sender_email="client@mail.ru",
        raw_payload_json=json.dumps({"xml_document": SAMPLE_XML}, ensure_ascii=False),
    )
    repo = MagicMock(spec=EmailRepository)
    repo.get_by_id.return_value = row

    with (
        patch("agent_pochta.api.app.get_session_factory") as mock_factory,
        patch("agent_pochta.api.app.EmailRepository", return_value=repo),
        patch("tempfile.NamedTemporaryFile", side_effect=AssertionError("disk write")),
        patch("tempfile.mkstemp", side_effect=AssertionError("disk write")),
        patch("tempfile.TemporaryFile", side_effect=AssertionError("disk write")),
    ):
        mock_factory.return_value.return_value.__enter__.return_value = MagicMock()
        client = TestClient(app)
        response = client.get(f"/api/v1/email-messages/{row_id}/xml")

    assert response.status_code == 200
    assert response.content == SAMPLE_XML.encode("utf-8")
    assert "application/xml" in response.headers["content-type"]
    disposition = response.headers["content-disposition"]
    assert "attachment" in disposition
    assert f"incoming_{str(row_id).replace('-', '')[:8]}.xml" in disposition


def test_download_xml_endpoint_404_when_missing():
    row_id = uuid.uuid4()
    row = EmailMessageRow(
        id=row_id,
        message_id="<no-xml@mail.ru>",
        received_at=datetime.now(timezone.utc).replace(tzinfo=None),
        mailbox="info@turbo-don.ru",
        sender_email="client@mail.ru",
        raw_payload_json=json.dumps({"message_id": "<no-xml@mail.ru>"}, ensure_ascii=False),
    )
    repo = MagicMock(spec=EmailRepository)
    repo.get_by_id.return_value = row

    with patch("agent_pochta.api.app.get_session_factory") as mock_factory:
        mock_factory.return_value.return_value.__enter__.return_value = MagicMock()
        with patch("agent_pochta.api.app.EmailRepository", return_value=repo):
            client = TestClient(app)
            response = client.get(f"/api/v1/email-messages/{row_id}/xml")

    assert response.status_code == 404
    assert "xml" in response.json()["detail"].lower()


def test_download_xml_endpoint_404_when_message_missing():
    row_id = uuid.uuid4()
    repo = MagicMock(spec=EmailRepository)
    repo.get_by_id.return_value = None

    with patch("agent_pochta.api.app.get_session_factory") as mock_factory:
        mock_factory.return_value.return_value.__enter__.return_value = MagicMock()
        with patch("agent_pochta.api.app.EmailRepository", return_value=repo):
            client = TestClient(app)
            response = client.get(f"/api/v1/email-messages/{row_id}/xml")

    assert response.status_code == 404
    assert response.json()["detail"] == "Message not found"
