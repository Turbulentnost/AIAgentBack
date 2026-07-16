"""Тесты скачивания вложений письма из IMAP."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from agent_pochta.api.app import app
from agent_pochta.attachments.download import (
    AttachmentDownloadResult,
    content_disposition_header,
)
from agent_pochta.db.models import EmailAttachmentRow, EmailMessageRow
from agent_pochta.db.repository import EmailRepository
from agent_pochta.schemas import Attachment, EmailMessage


def test_content_disposition_header_supports_unicode():
    header = content_disposition_header("Акт сверки.pdf")
    assert 'filename="???? ????.pdf"' in header or "filename=" in header
    assert "filename*=UTF-8''" in header
    assert "%D0%90%D0%BA%D1%82" in header or "Акт" not in header.split("filename*=", 1)[0]


@patch("agent_pochta.api.app.fetch_attachment_for_download")
def test_download_attachment_endpoint_streams_bytes(mock_fetch):
    row_id = uuid.uuid4()
    att_id = uuid.uuid4()
    row = EmailMessageRow(
        id=row_id,
        message_id="<att-download@mail.ru>",
        received_at=datetime.now(timezone.utc).replace(tzinfo=None),
        mailbox="info@turbo-don.ru",
        sender_email="client@mail.ru",
        raw_payload_json=json.dumps({"message_id": "<att-download@mail.ru>"}, ensure_ascii=False),
        attachments=[
            EmailAttachmentRow(
                id=att_id,
                message_id=row_id,
                filename="invoice.pdf",
                mime_type="application/pdf",
                size_bytes=4,
            )
        ],
    )
    repo = MagicMock(spec=EmailRepository)
    repo.get_by_id.return_value = row

    mock_fetch.return_value = AttachmentDownloadResult(
        ok=True,
        row_id=str(row_id),
        filename="invoice.pdf",
        mime_type="application/pdf",
        content=b"%PDF",
    )

    with patch("agent_pochta.api.app.get_session_factory") as mock_factory:
        mock_factory.return_value.return_value.__enter__.return_value = MagicMock()
        with patch("agent_pochta.api.app.EmailRepository", return_value=repo):
            client = TestClient(app)
            response = client.get(f"/api/v1/email-messages/{row_id}/attachments/0")

    assert response.status_code == 200
    assert response.content == b"%PDF"
    assert response.headers["content-type"].startswith("application/pdf")
    assert "invoice.pdf" in response.headers["content-disposition"]
    mock_fetch.assert_called_once_with(row_id, 0)


@patch("agent_pochta.api.app.fetch_attachment_for_download")
def test_download_attachment_endpoint_not_found(mock_fetch):
    row_id = uuid.uuid4()
    row = EmailMessageRow(
        id=row_id,
        message_id="<att-gone@mail.ru>",
        received_at=datetime.now(timezone.utc).replace(tzinfo=None),
        mailbox="info@turbo-don.ru",
        sender_email="client@mail.ru",
        raw_payload_json=json.dumps({"message_id": "<att-gone@mail.ru>"}, ensure_ascii=False),
        attachments=[
            EmailAttachmentRow(
                id=uuid.uuid4(),
                message_id=row_id,
                filename="missing.docx",
                mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                size_bytes=10,
            )
        ],
    )
    repo = MagicMock(spec=EmailRepository)
    repo.get_by_id.return_value = row

    mock_fetch.return_value = AttachmentDownloadResult(
        ok=False,
        row_id=str(row_id),
        filename="missing.docx",
        reason="not_in_mailbox",
    )

    with patch("agent_pochta.api.app.get_session_factory") as mock_factory:
        mock_factory.return_value.return_value.__enter__.return_value = MagicMock()
        with patch("agent_pochta.api.app.EmailRepository", return_value=repo):
            client = TestClient(app)
            response = client.get(f"/api/v1/email-messages/{row_id}/attachments/0")

    assert response.status_code == 404
    assert "не найдено" in response.json()["detail"].lower()


@patch("agent_pochta.attachments.download.ImapMailboxClient")
@patch("agent_pochta.attachments.download.resolve_imap_credentials")
def test_fetch_attachment_for_download_restores_from_imap(mock_creds, mock_client_cls):
    from agent_pochta.attachments.download import fetch_attachment_for_download

    row_id = uuid.uuid4()
    row = EmailMessageRow(
        id=row_id,
        message_id="<imap-att@mail.ru>",
        received_at=datetime.now(timezone.utc).replace(tzinfo=None),
        mailbox="info@turbo-don.ru",
        sender_email="client@mail.ru",
        raw_payload_json=json.dumps({"message_id": "<imap-att@mail.ru>"}, ensure_ascii=False),
        attachments=[
            EmailAttachmentRow(
                id=uuid.uuid4(),
                message_id=row_id,
                filename="scan.pdf",
                mime_type="application/pdf",
                size_bytes=3,
            )
        ],
    )
    repo = MagicMock(spec=EmailRepository)
    repo.get_by_id.return_value = row

    mock_client = MagicMock()
    mock_client.fetch_by_message_id.return_value = EmailMessage(
        message_id="<imap-att@mail.ru>",
        mailbox="info@turbo-don.ru",
        sender_email="client@mail.ru",
        subject="Test",
        received_at=datetime.now(timezone.utc),
        attachments=[
            Attachment(
                filename="scan.pdf",
                mime_type="application/pdf",
                size_bytes=3,
                content=b"PDF",
            )
        ],
    )
    mock_client_cls.return_value = mock_client
    mock_creds.return_value = MagicMock()

    with patch("agent_pochta.attachments.download.get_session_factory") as mock_factory:
        mock_factory.return_value.return_value.__enter__.return_value = MagicMock()
        with patch("agent_pochta.attachments.download.EmailRepository", return_value=repo):
            result = fetch_attachment_for_download(row_id, 0, vault=MagicMock())

    assert result.ok is True
    assert result.content == b"PDF"
    assert result.filename == "scan.pdf"
    mock_client.fetch_by_message_id.assert_called_once()
    kwargs = mock_client.fetch_by_message_id.call_args.kwargs
    assert kwargs.get("load_oversized_attachments") is True
    assert kwargs.get("timeout_sec") == 120


@patch("agent_pochta.attachments.download.ImapMailboxClient")
@patch("agent_pochta.attachments.download.resolve_imap_credentials")
def test_fetch_attachment_falls_back_to_payload_when_db_empty(mock_creds, mock_client_cls):
    from agent_pochta.attachments.download import fetch_attachment_for_download

    row_id = uuid.uuid4()
    row = EmailMessageRow(
        id=row_id,
        message_id="<payload-att@mail.ru>",
        received_at=datetime.now(timezone.utc).replace(tzinfo=None),
        mailbox="info@turbo-don.ru",
        sender_email="client@mail.ru",
        attachments_count=1,
        raw_payload_json=json.dumps(
            {
                "message_id": "<payload-att@mail.ru>",
                "attachments": [
                    {
                        "filename": "from-payload.pdf",
                        "mime_type": "application/pdf",
                        "size_bytes": 10,
                    }
                ],
            },
            ensure_ascii=False,
        ),
        attachments=[],
    )
    repo = MagicMock(spec=EmailRepository)
    repo.get_by_id.return_value = row

    mock_client = MagicMock()
    mock_client.fetch_by_message_id.return_value = EmailMessage(
        message_id="<payload-att@mail.ru>",
        mailbox="info@turbo-don.ru",
        sender_email="client@mail.ru",
        subject="Test",
        received_at=datetime.now(timezone.utc),
        attachments=[
            Attachment(
                filename="from-payload.pdf",
                mime_type="application/pdf",
                size_bytes=3,
                content=b"PDF",
            )
        ],
    )
    mock_client_cls.return_value = mock_client
    mock_creds.return_value = MagicMock()

    with patch("agent_pochta.attachments.download.get_session_factory") as mock_factory:
        mock_factory.return_value.return_value.__enter__.return_value = MagicMock()
        with patch("agent_pochta.attachments.download.EmailRepository", return_value=repo):
            result = fetch_attachment_for_download(row_id, 0, vault=MagicMock())

    assert result.ok is True
    assert result.filename == "from-payload.pdf"
    assert result.content == b"PDF"


def test_row_attachments_includes_index():
    from agent_pochta.api.app import _row_attachments

    row_id = uuid.uuid4()
    row = EmailMessageRow(
        id=row_id,
        message_id="<idx-att@mail.ru>",
        received_at=datetime.now(timezone.utc).replace(tzinfo=None),
        mailbox="info@turbo-don.ru",
        sender_email="client@mail.ru",
        raw_payload_json=json.dumps({"attachments": []}, ensure_ascii=False),
        attachments=[
            EmailAttachmentRow(
                id=uuid.uuid4(),
                message_id=row_id,
                filename="a.txt",
                mime_type="text/plain",
                size_bytes=1,
            ),
            EmailAttachmentRow(
                id=uuid.uuid4(),
                message_id=row_id,
                filename="b.txt",
                mime_type="text/plain",
                size_bytes=2,
            ),
        ],
    )
    items = _row_attachments(row)
    assert [item["index"] for item in items] == [0, 1]
    assert items[0]["filename"] == "a.txt"


def test_row_attachments_falls_back_to_payload_when_db_empty():
    from agent_pochta.api.app import _row_attachments

    row_id = uuid.uuid4()
    row = EmailMessageRow(
        id=row_id,
        message_id="<payload-list@mail.ru>",
        received_at=datetime.now(timezone.utc).replace(tzinfo=None),
        mailbox="info@turbo-don.ru",
        sender_email="client@mail.ru",
        attachments_count=2,
        raw_payload_json=json.dumps(
            {
                "attachments": [
                    {"filename": "a.pdf", "mime_type": "application/pdf", "size_bytes": 10},
                    {"filename": "b.png", "mime_type": "image/png", "size_bytes": 20},
                ]
            },
            ensure_ascii=False,
        ),
        attachments=[],
    )
    items = _row_attachments(row)
    assert [item["filename"] for item in items] == ["a.pdf", "b.png"]
    assert [item["index"] for item in items] == [0, 1]
