"""Извлечение и подготовка текста из вложений для LLM."""

from agent_pochta.attachments.extract import (
    SUPPORTED_MIME,
    extract_attachment_text,
    is_meaningful_extracted_text,
    is_supported_attachment,
    normalize_extracted_text,
    resolve_mime_type,
)
from agent_pochta.attachments.pipeline import (
    AttachmentProcessingResult,
    attachment_storage_metadata,
    process_email_attachments,
)

__all__ = [
    "SUPPORTED_MIME",
    "AttachmentProcessingResult",
    "attachment_storage_metadata",
    "extract_attachment_text",
    "is_meaningful_extracted_text",
    "is_supported_attachment",
    "normalize_extracted_text",
    "process_email_attachments",
    "resolve_mime_type",
]
