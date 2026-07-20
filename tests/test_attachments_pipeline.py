"""Тесты модуля attachments (pipeline + storage metadata)."""

from __future__ import annotations

from datetime import datetime, timezone

from agent_pochta.attachments.pipeline import (
    attachment_storage_metadata,
    process_email_attachments,
)
from agent_pochta.schemas import Attachment, EmailMessage
from agent_pochta.services.integration_service import StubIntegrationService
from agent_pochta.services.llm_gateway import StubLLMGateway
from agent_pochta.services.local_document import LocalDocumentService
from agent_pochta.services.rag import StubRAGService
from agent_pochta.services.vault import StubVaultClient
from agent_pochta.services import ServiceContainer


def test_attachment_storage_metadata_includes_excerpt():
    att = Attachment(
        filename="schet.txt",
        mime_type="text/plain",
        size_bytes=20,
        content=b"1234567890" * 300,
        extracted_text="1234567890" * 300,
    )
    meta = attachment_storage_metadata(att, excerpt_chars=100)
    assert meta["has_text"] is True
    assert len(meta["text_excerpt"]) == 100


def test_process_email_attachments_builds_combined_text():
    content = "Счёт № 4521 на оплату.".encode("utf-8")
    email = EmailMessage(
        message_id="<att@example>",
        mailbox="test@turbo-don.ru",
        sender_email="client@example.com",
        subject="Счёт",
        body_text="Во вложении.",
        received_at=datetime.now(timezone.utc),
        attachments=[
            Attachment(
                filename="schet.txt",
                mime_type="text/plain",
                size_bytes=len(content),
                content=content,
            )
        ],
    )
    container = ServiceContainer(
        llm=StubLLMGateway(),
        documents=LocalDocumentService(),
        integration=StubIntegrationService(),
        rag=StubRAGService(),
        vault=StubVaultClient(),
    )
    result = process_email_attachments(email, container.documents)
    assert "4521" in result.combined_text
    assert "4521" in result.attachments_text
    assert "=== ВЛОЖЕНИЯ" in result.attachments_text
    assert result.extraction_meta[0]["has_text"] is True


def test_process_email_attachments_zip_inner_txt():
    import zipfile
    from io import BytesIO

    inner = "Счёт Деловые Линии № 26-00725020933 на оплату.".encode("utf-8")
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("schet.txt", inner)
    content = buf.getvalue()

    email = EmailMessage(
        message_id="<zip@example>",
        mailbox="test@turbo-don.ru",
        sender_email="client@example.com",
        subject="Счёт",
        body_text="См. архив.",
        received_at=datetime.now(timezone.utc),
        attachments=[
            Attachment(
                filename="schet.zip",
                mime_type="application/zip",
                size_bytes=len(content),
                content=content,
            )
        ],
    )
    container = ServiceContainer(
        llm=StubLLMGateway(),
        documents=LocalDocumentService(),
        integration=StubIntegrationService(),
        rag=StubRAGService(),
        vault=StubVaultClient(),
    )
    result = process_email_attachments(email, container.documents)
    assert "26-00725020933" in result.combined_text
    assert result.extraction_meta[0]["has_text"] is True


def test_process_email_without_attachments_unchanged():
    email = EmailMessage(
        message_id="<noatt@example>",
        mailbox="test@turbo-don.ru",
        sender_email="client@example.com",
        subject="Вопрос",
        body_text="Только текст письма.",
        received_at=datetime.now(timezone.utc),
    )
    container = ServiceContainer(
        llm=StubLLMGateway(),
        documents=LocalDocumentService(),
        integration=StubIntegrationService(),
        rag=StubRAGService(),
        vault=StubVaultClient(),
    )
    result = process_email_attachments(email, container.documents)
    assert result.combined_text == "Вопрос\n\nТолько текст письма."
    assert result.attachments_text == ""
    assert result.extraction_meta == []
