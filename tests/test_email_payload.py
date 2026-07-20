"""Тест сериализации Celery payload."""

from __future__ import annotations

from datetime import datetime, timezone

from agent_pochta.email_payload import (
    BODY_NOT_STORED_PLACEHOLDER,
    email_from_task_payload,
    email_to_task_payload,
    sanitize_payload_for_storage,
)
from agent_pochta.schemas import Attachment, EmailMessage


def test_email_payload_roundtrip_with_binary_attachment():
    email = EmailMessage(
        message_id="<bin@test>",
        mailbox="info@turbo-don.ru",
        sender_email="a@b.ru",
        subject="Test",
        body_text="Hello",
        received_at=datetime.now(timezone.utc),
        attachments=[
            Attachment(
                filename="doc.pdf",
                mime_type="application/pdf",
                size_bytes=4,
                content=b"test",
            )
        ],
    )
    restored = email_from_task_payload(email_to_task_payload(email))
    assert restored.attachments[0].content == b"test"


def test_email_to_task_payload_for_storage_omits_body_and_binary():
    email = EmailMessage(
        message_id="<store@test>",
        mailbox="info@turbo-don.ru",
        sender_email="a@b.ru",
        sender_name="Alice",
        subject="Счёт",
        body_text="Секретный текст",
        body_html="<p>HTML</p>",
        received_at=datetime.now(timezone.utc),
        to=["buh@turbo-don.ru"],
        cc=["jurist@turbo-don.ru"],
        routing_recipient="buh@turbo-don.ru",
        attachments=[
            Attachment(
                filename="doc.pdf",
                mime_type="application/pdf",
                size_bytes=4,
                content=b"test",
                extracted_text="Текст из PDF",
                ocr_used=True,
            )
        ],
    )
    stored = email_to_task_payload(email, for_storage=True)
    assert stored["body_text"] == ""
    assert "body_html" not in stored
    assert stored["message_id"] == "<store@test>"
    assert stored["sender_email"] == "a@b.ru"
    assert stored["to"] == ["buh@turbo-don.ru"]
    assert stored["cc"] == ["jurist@turbo-don.ru"]
    assert stored["routing_recipient"] == "buh@turbo-don.ru"
    assert stored["attachments"] == [
        {
            "filename": "doc.pdf",
            "mime_type": "application/pdf",
            "size_bytes": 4,
            "ocr_used": True,
            "has_text": True,
            "text_excerpt": "Текст из PDF",
            "extraction_error": None,
        }
    ]

    sanitized = sanitize_payload_for_storage(email_to_task_payload(email))
    assert sanitized["body_text"] == ""
    assert sanitized["attachments"][0]["filename"] == "doc.pdf"
    assert "content" not in sanitized["attachments"][0]
