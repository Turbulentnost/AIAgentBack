"""Парсинг MIME-сообщений в EmailMessage."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from email import policy
from email.header import decode_header, make_header
from email.parser import BytesParser
from email.utils import getaddresses, parseaddr, parsedate_to_datetime

from agent_pochta.config import get_settings
from agent_pochta.schemas import Attachment, EmailMessage


def _decode_header_value(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def _html_to_text(html: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", "", html)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</p>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _extract_bodies(message) -> tuple[str, str | None]:
    body_text = ""
    body_html: str | None = None

    if message.is_multipart():
        for part in message.walk():
            if part.get_content_disposition() == "attachment":
                continue
            content_type = part.get_content_type()
            try:
                payload = part.get_content()
            except Exception:
                continue
            if not isinstance(payload, str):
                continue
            if content_type == "text/plain" and not body_text:
                body_text = payload.strip()
            elif content_type == "text/html" and body_html is None:
                body_html = payload.strip()
    else:
        try:
            payload = message.get_content()
        except Exception:
            payload = ""
        if isinstance(payload, str):
            if message.get_content_type() == "text/html":
                body_html = payload.strip()
            else:
                body_text = payload.strip()

    if not body_text and body_html:
        body_text = _html_to_text(body_html)
    return body_text, body_html


def _extract_attachments(message) -> list[Attachment]:
    max_bytes = get_settings().max_attachment_mb * 1024 * 1024
    attachments: list[Attachment] = []

    for part in message.walk():
        filename = part.get_filename()
        disposition = (part.get_content_disposition() or "").lower()
        if not filename and disposition != "attachment":
            continue
        filename = _decode_header_value(filename) or "attachment.bin"
        mime_type = part.get_content_type()
        raw = part.get_payload(decode=True) or b""
        size_bytes = len(raw)
        content = raw if size_bytes <= max_bytes else None
        if size_bytes > max_bytes:
            size_bytes = len(raw)
        attachments.append(
            Attachment(
                filename=filename,
                mime_type=mime_type,
                size_bytes=size_bytes,
                content=content,
            )
        )
    return attachments


def parse_raw_email(raw: bytes, mailbox: str) -> EmailMessage:
    """Преобразует сырое IMAP-сообщение в доменную модель."""
    message = BytesParser(policy=policy.default).parsebytes(raw)

    message_id = (message.get("Message-ID") or "").strip()
    if not message_id:
        message_id = f"<generated-{hash(raw) & 0xFFFFFFFF:x}@{mailbox}>"

    sender_name, sender_email = parseaddr(message.get("From", ""))
    sender_name = _decode_header_value(sender_name) or None
    sender_email = sender_email.lower().strip()

    subject = _decode_header_value(message.get("Subject", ""))
    received_at = datetime.now(timezone.utc)
    if date_header := message.get("Date"):
        try:
            received_at = parsedate_to_datetime(date_header)
            if received_at.tzinfo is None:
                received_at = received_at.replace(tzinfo=timezone.utc)
        except Exception:
            pass

    to_addrs = [addr.lower() for _name, addr in getaddresses(message.get_all("To", [])) if addr]
    cc = [addr.lower() for _name, addr in getaddresses(message.get_all("Cc", [])) if addr]
    reply_to = message.get("Reply-To")
    reply_to_addr = parseaddr(reply_to)[1].lower().strip() if reply_to else None
    list_unsubscribe = message.get("List-Unsubscribe")

    body_text, body_html = _extract_bodies(message)
    attachments = _extract_attachments(message)

    return EmailMessage(
        message_id=message_id,
        mailbox=mailbox,
        sender_email=sender_email,
        sender_name=sender_name,
        subject=subject,
        body_text=body_text,
        body_html=body_html,
        received_at=received_at,
        to=to_addrs,
        cc=cc,
        reply_to=reply_to_addr or None,
        list_unsubscribe=list_unsubscribe,
        attachments=attachments,
    )
