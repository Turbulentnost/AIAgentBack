"""Формирование и пост-обработка краткого обзора письма (узел 6)."""

from __future__ import annotations

import re
from typing import Any

from agent_pochta.config import Settings, get_settings
from agent_pochta.schemas import EmailMessage, RoutingResult, SenderIdentity

_SIGNATURE_RE = re.compile(
    r"\n\s*(-{2,}|_{4,}|с уважением|best regards|kind regards|sent from my|отправлено с iphone)",
    re.IGNORECASE,
)


def prepare_text_for_summary(text: str, *, max_chars: int = 12_000) -> str:
    """Нормализует и слегка укорачивает исходный текст перед LLM."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    match = _SIGNATURE_RE.search(normalized)
    if match and match.start() > 40:
        normalized = normalized[: match.start()].strip()
    if len(normalized) > max_chars:
        normalized = normalized[:max_chars].rsplit("\n", 1)[0].strip()
    return normalized


def _build_attachments_text_from_email(email: EmailMessage) -> str:
    from agent_pochta.attachments.extract import is_meaningful_extracted_text
    from agent_pochta.attachments.pipeline import ATTACHMENTS_HEADER, attachment_placeholder

    if not email.attachments:
        return ""

    parts: list[str] = []
    for att in email.attachments:
        if att.extracted_text and is_meaningful_extracted_text(att.extracted_text):
            parts.append(f"--- {att.filename} ({att.mime_type}) ---\n{att.extracted_text}")
        elif att.filename:
            parts.append(attachment_placeholder(att))

    if not parts:
        return ""
    return f"{ATTACHMENTS_HEADER.format(count=len(email.attachments))}\n\n" + "\n\n".join(parts)


def build_summary_context(
    email: EmailMessage,
    combined_text: str,
    *,
    routing: RoutingResult | None = None,
    sender: SenderIdentity | None = None,
    attachments_text: str = "",
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Контекст для промпта суммаризации."""
    from agent_pochta.attachments.extract import is_meaningful_extracted_text

    settings = settings or get_settings()

    attachment_names = [a.filename for a in email.attachments if a.filename]
    attachment_summaries = [
        {
            "filename": att.filename,
            "mime_type": att.mime_type,
            "ocr_used": att.ocr_used,
            "has_text": is_meaningful_extracted_text(att.extracted_text),
            "text_excerpt": (att.extracted_text or "")[:500] or None,
        }
        for att in email.attachments
    ]

    if not attachments_text:
        attachments_text = _build_attachments_text_from_email(email)

    body_source = "\n\n".join(p for p in [email.subject, email.body_text] if p)
    if not body_source.strip():
        body_source = combined_text or email.body_text

    body_only = prepare_text_for_summary(
        body_source,
        max_chars=settings.summary_body_max_chars,
    )
    attachments_section = (
        prepare_text_for_summary(
            attachments_text,
            max_chars=settings.summary_attachments_max_chars,
        )
        if attachments_text
        else ""
    )

    if attachments_section:
        body_and_attachments = f"{body_only}\n\n{attachments_section}"
    else:
        body_and_attachments = body_only

    ctx: dict[str, Any] = {
        "sender_name": email.sender_name or "",
        "sender_email": email.sender_email,
        "subject": email.subject,
        "body_and_attachments": body_and_attachments,
        "attachments_text": attachments_section,
        "attachments_count": len(email.attachments),
        "attachment_names": attachment_names,
        "attachments": attachment_summaries,
        "has_attachment_text": any(item["has_text"] for item in attachment_summaries),
    }
    if sender and sender.found and sender.contractor:
        ctx["contractor_name"] = sender.contractor.name
        ctx["contractor_type"] = sender.contractor.contractor_type
    if routing:
        ctx["department_name"] = routing.department_name
        ctx["priority"] = routing.priority.value
    return ctx


def clamp_summary(text: str, *, max_sentences: int = 5, max_chars: int = 800) -> str:
    """Ограничивает обзор 3–5 предложениями (по ТЗ) и максимальной длиной."""
    cleaned = " ".join(text.split()).strip()
    if not cleaned:
        return cleaned

    parts = re.split(r"(?<=[.!?…])\s+", cleaned)
    sentences = [s.strip() for s in parts if s.strip()]
    if len(sentences) > max_sentences:
        sentences = sentences[:max_sentences]
    result = " ".join(sentences)

    if len(result) > max_chars:
        truncated = result[: max_chars - 1]
        if " " in truncated:
            truncated = truncated.rsplit(" ", 1)[0]
        result = f"{truncated}…"
    return result
