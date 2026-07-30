"""Тесты извлечения текста из вложений."""

from __future__ import annotations

import pytest

from agent_pochta.schemas import Attachment
from agent_pochta.attachments.extract import extract_pdf
from agent_pochta.services.document_extract import (
    extract_attachment_text,
    extract_plain_text,
    is_supported_attachment,
    normalize_extracted_text,
    resolve_mime_type,
)
from agent_pochta.services.local_document import LocalDocumentService


def test_resolve_mime_by_extension():
    att = Attachment(
        filename="akt_sverki.pdf",
        mime_type="application/octet-stream",
        size_bytes=100,
        content=b"%PDF",
    )
    assert resolve_mime_type(att) == "application/pdf"
    assert is_supported_attachment(att)


def test_normalize_truncates_long_text():
    text = "слово " * 5000
    result = normalize_extracted_text(text, max_chars=200)
    assert len(result) <= 201
    assert result.endswith("…")


def test_extract_plain_text_attachment():
    content = "Акт сверки за 2 квартал 2026. Сумма: 1 250 000 руб.".encode("utf-8")
    att = Attachment(
        filename="akt.txt",
        mime_type="text/plain",
        size_bytes=len(content),
        content=content,
    )
    text, ocr = extract_attachment_text(att, max_chars=1000)
    assert ocr is False
    assert text is not None
    assert "Акт сверки" in text


def test_local_document_service_pdf():
    pymupdf = pytest.importorskip("fitz")
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Invoice 4521 payment required")
    pdf_bytes = doc.tobytes()
    doc.close()

    att = Attachment(
        filename="pretensiya.pdf",
        mime_type="application/pdf",
        size_bytes=len(pdf_bytes),
        content=pdf_bytes,
    )
    service = LocalDocumentService(max_extract_chars=5000)
    result = service.extract(att)
    assert result.extracted_text
    assert "4521" in result.extracted_text
    assert result.ocr_used is False


def test_extract_pdf_text_layer_skips_ocr(monkeypatch):
    pymupdf = pytest.importorskip("fitz")
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Invoice 4521 payment required")
    pdf_bytes = doc.tobytes()
    doc.close()

    def fail_ocr(*args, **kwargs):
        raise AssertionError("OCR should not be called for text-layer PDF")

    monkeypatch.setattr(
        "agent_pochta.attachments.extract._ocr_pil_image",
        fail_ocr,
    )

    text, ocr_used = extract_pdf(pdf_bytes)
    assert ocr_used is False
    assert "4521" in text


def test_extract_pdf_ocr_fallback_when_no_text_layer(monkeypatch):
    pymupdf = pytest.importorskip("fitz")
    doc = pymupdf.open()
    doc.new_page()
    pdf_bytes = doc.tobytes()
    doc.close()

    monkeypatch.setattr(
        "agent_pochta.attachments.extract._ocr_pil_image",
        lambda image: "Сканированный счёт № 12345",
    )

    text, ocr_used = extract_pdf(pdf_bytes)
    assert ocr_used is True
    assert "12345" in text

    att = Attachment(
        filename="scan.pdf",
        mime_type="application/pdf",
        size_bytes=len(pdf_bytes),
        content=pdf_bytes,
    )
    attachment_text, attachment_ocr = extract_attachment_text(att, max_chars=1000)
    assert attachment_ocr is True
    assert attachment_text is not None
    assert "12345" in attachment_text


def test_local_document_service_docx():
    pytest.importorskip("docx")
    from docx import Document

    buffer = __import__("io").BytesIO()
    document = Document()
    document.add_paragraph("Счёт № 4521 на оплату комплектующих")
    document.save(buffer)

    content = buffer.getvalue()
    att = Attachment(
        filename="schet.docx",
        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        size_bytes=len(content),
        content=content,
    )
    service = LocalDocumentService()
    result = service.extract(att)
    assert result.extracted_text
    assert "4521" in result.extracted_text


def test_process_content_includes_attachment_in_combined_text():
    from datetime import datetime, timezone

    from agent_pochta.nodes.n4_process_content import node_process_content
    from agent_pochta.schemas import EmailMessage
    from agent_pochta.services import ServiceContainer
    from agent_pochta.services.integration_service import StubIntegrationService
    from agent_pochta.services.llm_gateway import StubLLMGateway
    from agent_pochta.services.local_document import LocalDocumentService
    from agent_pochta.services.rag import StubRAGService
    from agent_pochta.services.vault import StubVaultClient

    content = "Требуется согласование договора поставки.".encode("utf-8")
    email = EmailMessage(
        message_id="<doc@example>",
        mailbox="test@turbo-don.ru",
        sender_email="client@example.com",
        subject="Договор",
        body_text="Добрый день, во вложении.",
        received_at=datetime.now(timezone.utc),
        attachments=[
            Attachment(
                filename="dogovor.txt",
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
    state = node_process_content({"email": email, "trace": []}, container=container)
    combined = state["combined_text"]
    assert "согласование договора" in combined
    assert "=== ВЛОЖЕНИЯ (1) — извлечённый текст ===" in combined
    assert "--- dogovor.txt (text/plain) ---" in combined
