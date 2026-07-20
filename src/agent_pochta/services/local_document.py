"""Локальный Document Service — парсинг вложений внутри агента."""

from __future__ import annotations

from agent_pochta.schemas import Attachment
from agent_pochta.attachments.extract import (
    extract_attachment_text,
    is_supported_attachment,
    resolve_mime_type,
)
from agent_pochta.services.document_service import DocumentService


class LocalDocumentService(DocumentService):
    """Извлекает текст из PDF/DOCX/XLSX/изображений без HTTP Document Service."""

    def __init__(
        self,
        *,
        max_attachment_mb: int = 25,
        max_extract_chars: int = 12_000,
    ) -> None:
        self._max_bytes = max_attachment_mb * 1024 * 1024
        self._max_extract_chars = max_extract_chars

    def extract(self, attachment: Attachment) -> Attachment:
        if attachment.size_bytes > self._max_bytes:
            attachment.extracted_text = None
            attachment.ocr_used = False
            return attachment

        if attachment.content is None:
            return attachment

        mime = resolve_mime_type(attachment)
        if not is_supported_attachment(attachment):
            attachment.extracted_text = (
                f"[Вложение «{attachment.filename}»: формат {mime} не поддерживается для извлечения текста]"
            )
            attachment.ocr_used = False
            return attachment

        text, ocr_used = extract_attachment_text(
            attachment,
            max_chars=self._max_extract_chars,
        )
        attachment.extracted_text = text
        attachment.ocr_used = ocr_used
        if text is None and mime.startswith("image/"):
            attachment.extracted_text = (
                f"[Изображение «{attachment.filename}»: OCR не распознал текст]"
            )
        return attachment
