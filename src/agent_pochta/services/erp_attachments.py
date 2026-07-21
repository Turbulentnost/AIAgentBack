"""Прикрепление вложений письма к документу 1С после create_incoming_correspondence."""

from __future__ import annotations

import structlog

from agent_pochta.schemas import Attachment, EmailMessage
from agent_pochta.services.integration_service import IntegrationService
from agent_pochta.services.odata_attached_file import AttachedFileInput

logger = structlog.get_logger(__name__)


def attachments_with_content(email: EmailMessage) -> list[Attachment]:
    return [
        att
        for att in (email.attachments or [])
        if att.content and len(att.content) > 0 and (att.filename or "").strip()
    ]


def attach_email_files_to_document(
    integration: IntegrationService,
    *,
    document_ref_key: str,
    email: EmailMessage,
) -> list[dict]:
    """Прикрепляет вложения с content к существующему документу 1С."""
    attach_fn = getattr(integration, "attach_files_to_incoming_correspondence", None)
    if not callable(attach_fn):
        return []

    files = [
        AttachedFileInput(filename=att.filename, content=bytes(att.content))
        for att in attachments_with_content(email)
    ]
    if not files:
        return []

    try:
        results = attach_fn(document_ref_key=document_ref_key, files=files)
    except Exception:
        logger.exception(
            "erp_attach_files_failed",
            document_ref_key=document_ref_key,
            message_id=email.message_id,
            files=len(files),
        )
        raise

    logger.info(
        "erp_attach_files_done",
        document_ref_key=document_ref_key,
        message_id=email.message_id,
        attached=len(results),
    )
    return results
