"""Оркестрация извлечения текста из вложений для узла 4 и LLM."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import structlog

from agent_pochta.config import Settings, get_settings
from agent_pochta.schemas import Attachment, EmailMessage
from agent_pochta.services.document_service import DocumentService

logger = structlog.get_logger(__name__)

ATTACHMENTS_HEADER = "=== ВЛОЖЕНИЯ ({count}) — извлечённый текст ==="


@dataclass
class AttachmentProcessingResult:
    combined_text: str
    attachments_text: str
    extraction_meta: list[dict[str, Any]] = field(default_factory=list)


def _truncate_total(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    truncated = text[: max_chars - 1]
    if "\n\n" in truncated[-200:]:
        truncated = truncated.rsplit("\n\n", 1)[0]
    elif " " in truncated[-80:]:
        truncated = truncated.rsplit(" ", 1)[0]
    return f"{truncated}…"


def attachment_placeholder(att: Attachment) -> str:
    return (
        f"[Вложение {att.filename}, {att.mime_type}, "
        f"{att.size_bytes} байт — текст не извлечён]"
    )


def _format_attachment_block(att: Attachment, text: str) -> str:
    return f"--- {att.filename} ({att.mime_type}) ---\n{text}"


def process_email_attachments(
    email: EmailMessage,
    document_service: DocumentService,
    *,
    settings: Settings | None = None,
) -> AttachmentProcessingResult:
    """Извлекает текст из всех вложений и собирает контекст для LLM/RAG."""
    settings = settings or get_settings()
    total_limit = settings.document_extract_total_max_chars

    body_parts: list[str] = [email.subject, email.body_text]
    attachment_parts: list[str] = []
    extraction_meta: list[dict[str, Any]] = []

    for att in email.attachments:
        meta: dict[str, Any] = {
            "filename": att.filename,
            "mime_type": att.mime_type,
            "size_bytes": att.size_bytes,
            "ocr_used": False,
            "has_text": False,
            "extraction_error": None,
        }
        try:
            if att.content is None and att.size_bytes > 0:
                meta["extraction_error"] = "missing_content"
                logger.warning(
                    "attachment_missing_content",
                    filename=att.filename,
                    mime_type=att.mime_type,
                    size_bytes=att.size_bytes,
                    message_id=email.message_id,
                )
                attachment_parts.append(attachment_placeholder(att))
                extraction_meta.append(meta)
                continue

            processed = document_service.extract(att)
            att.extracted_text = processed.extracted_text
            att.ocr_used = processed.ocr_used
            meta["ocr_used"] = processed.ocr_used
            meta["has_text"] = bool(processed.extracted_text)
            if processed.extracted_text:
                block = _format_attachment_block(att, processed.extracted_text)
                attachment_parts.append(block)
            elif att.filename:
                placeholder = attachment_placeholder(att)
                attachment_parts.append(placeholder)
                meta["extraction_error"] = "no_text"
                logger.info(
                    "attachment_no_extractable_text",
                    filename=att.filename,
                    mime_type=att.mime_type,
                    size_bytes=att.size_bytes,
                    ocr_used=processed.ocr_used,
                    message_id=email.message_id,
                )
        except Exception as exc:
            logger.warning(
                "attachment_extract_failed",
                filename=att.filename,
                mime_type=att.mime_type,
                error=str(exc),
                message_id=email.message_id,
            )
            att.extracted_text = None
            att.ocr_used = False
            meta["extraction_error"] = str(exc)
            if att.filename:
                attachment_parts.append(attachment_placeholder(att))
        extraction_meta.append(meta)

    attachments_body = "\n\n".join(attachment_parts)
    if attachments_body:
        attachments_text = _truncate_total(
            f"{ATTACHMENTS_HEADER.format(count=len(email.attachments))}\n\n{attachments_body}",
            total_limit,
        )
    else:
        attachments_text = ""

    all_parts = body_parts + ([attachments_text] if attachments_text else [])
    combined_text = "\n\n".join(p for p in all_parts if p)

    return AttachmentProcessingResult(
        combined_text=combined_text,
        attachments_text=attachments_text,
        extraction_meta=extraction_meta,
    )


def attachment_storage_metadata(
    attachment: Attachment,
    *,
    excerpt_chars: int,
    extraction_error: str | None = None,
) -> dict[str, Any]:
    """Метаданные вложения для raw_payload_json (без бинарного content)."""
    from agent_pochta.attachments.extract import is_meaningful_extracted_text

    text = attachment.extracted_text
    meta: dict[str, Any] = {
        "filename": attachment.filename,
        "mime_type": attachment.mime_type,
        "size_bytes": attachment.size_bytes,
        "ocr_used": attachment.ocr_used,
        "has_text": is_meaningful_extracted_text(text),
        "extraction_error": extraction_error,
    }
    if text:
        meta["text_excerpt"] = text[:excerpt_chars]
    return meta
