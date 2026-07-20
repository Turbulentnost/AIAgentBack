"""Сериализация EmailMessage для Celery (bytes ↔ hex) и хранения в PostgreSQL."""

from __future__ import annotations

import json

from agent_pochta.config import get_settings
from agent_pochta.schemas import Attachment, EmailMessage

BODY_NOT_STORED_PLACEHOLDER = "Текст не хранится"

_STORAGE_ATTACHMENT_KEYS = frozenset(
    {
        "filename",
        "mime_type",
        "size_bytes",
        "ocr_used",
        "has_text",
        "text_excerpt",
        "extraction_error",
    }
)


def _attachment_for_storage(attachment: Attachment) -> dict:
    from agent_pochta.attachments.pipeline import attachment_storage_metadata

    settings = get_settings()
    return attachment_storage_metadata(
        attachment,
        excerpt_chars=settings.document_storage_excerpt_chars,
    )


def sanitize_payload_for_storage(payload: dict) -> dict:
    """Удаляет тело письма и бинарное содержимое вложений из JSON для PostgreSQL."""
    data = dict(payload)
    data["body_text"] = ""
    data.pop("body_html", None)

    attachments: list[dict] = []
    for attachment in data.get("attachments") or []:
        if not isinstance(attachment, dict):
            continue
        attachments.append(
            {key: attachment[key] for key in _STORAGE_ATTACHMENT_KEYS if key in attachment}
        )
    data["attachments"] = attachments
    return data


def email_to_task_payload(email: EmailMessage, *, for_storage: bool = False) -> dict:
    """Полный payload для Celery; при for_storage=True — только метаданные для БД."""
    payload = email.model_dump(mode="python")
    stored_attachments: list[dict] = []
    for attachment in payload.get("attachments") or []:
        content = attachment.get("content")
        if for_storage:
            attachment.pop("content", None)
            extracted_text = attachment.pop("extracted_text", None)
            att_obj = Attachment.model_validate(attachment)
            if extracted_text:
                att_obj.extracted_text = extracted_text
            stored_attachments.append(_attachment_for_storage(att_obj))
        elif isinstance(content, (bytes, bytearray)):
            attachment["content"] = bytes(content).hex()
        elif content is None:
            attachment.pop("content", None)

    if for_storage:
        payload["attachments"] = stored_attachments
        payload = sanitize_payload_for_storage(payload)

    return json.loads(json.dumps(payload, default=str))


def email_from_task_payload(payload: dict) -> EmailMessage:
    data = dict(payload)
    for attachment in data.get("attachments") or []:
        content = attachment.get("content")
        if isinstance(content, str) and content:
            attachment["content"] = bytes.fromhex(content)
    return EmailMessage.model_validate(data)
