"""Document Service — извлечение текста из вложений (узел 4, раздел 5.4 ТЗ).

Поддержка: PDF (PyMuPDF), DOCX (python-docx), XLSX (openpyxl),
изображения (OCR Tesseract + Vision LLM). Лимит — 25 МБ.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from agent_pochta.schemas import Attachment


class DocumentService(ABC):
    @abstractmethod
    def extract(self, attachment: Attachment) -> Attachment:
        """Возвращает вложение с заполненными extracted_text / ocr_used."""


# MIME-типы, которые умеем обрабатывать (раздел 4, узел 4)
SUPPORTED_MIME = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",  # docx
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",        # xlsx
    "image/jpeg",
    "image/png",
    "image/gif",
}


class StubDocumentService(DocumentService):
    """Заглушка: не парсит реальные файлы, но соблюдает контракт и лимиты."""

    def extract(self, attachment: Attachment) -> Attachment:
        max_bytes = 25 * 1024 * 1024
        if attachment.size_bytes > max_bytes:
            attachment.extracted_text = None  # только метаданные при превышении
            return attachment
        if attachment.mime_type not in SUPPORTED_MIME:
            attachment.extracted_text = None  # фиксируем только имя и тип
            return attachment
        is_image = attachment.mime_type.startswith("image/")
        attachment.ocr_used = is_image
        attachment.extracted_text = (
            f"[Заглушка извлечения текста из «{attachment.filename}» "
            f"({attachment.mime_type}); реальный парсинг — через Document Service платформы.]"
        )
        return attachment
