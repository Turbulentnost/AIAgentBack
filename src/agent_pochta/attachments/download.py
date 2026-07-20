"""On-demand скачивание вложений письма из IMAP (байты в БД не хранятся)."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from urllib.parse import quote

import structlog

from agent_pochta.attachments.cache import (
    attachment_cache_key,
    get_cached_attachment,
    put_cached_attachment,
)
from agent_pochta.config import get_settings
from agent_pochta.db.repository import EmailRepository
from agent_pochta.db.session import get_session_factory
from agent_pochta.imap.client import ImapMailboxClient, resolve_imap_credentials
from agent_pochta.imap.body_fetch import resolve_imap_message_id
from agent_pochta.services.vault import VaultClient

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class AttachmentDownloadResult:
    ok: bool
    row_id: str
    filename: str = ""
    mime_type: str = "application/octet-stream"
    content: bytes | None = None
    reason: str | None = None


@dataclass(frozen=True)
class _AttachmentRef:
    filename: str
    mime_type: str | None = None
    size_bytes: int | None = None


def content_disposition_header(filename: str) -> str:
    """Content-Disposition с поддержкой не-ASCII имён (RFC 5987)."""
    safe = (filename or "attachment").replace('"', "").replace("\r", "").replace("\n", "")
    ascii_name = safe.encode("ascii", "replace").decode("ascii") or "attachment"
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(safe)}"


def _payload_attachment_refs(raw_payload_json: str | None) -> list[_AttachmentRef]:
    if not raw_payload_json:
        return []
    try:
        payload = json.loads(raw_payload_json)
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, dict):
        return []
    refs: list[_AttachmentRef] = []
    for index, item in enumerate(payload.get("attachments") or []):
        if not isinstance(item, dict):
            continue
        filename = str(item.get("filename") or "").strip() or f"attachment-{index}"
        mime_type = item.get("mime_type")
        size = item.get("size_bytes")
        try:
            size_bytes = int(size) if size is not None else None
        except (TypeError, ValueError):
            size_bytes = None
        refs.append(
            _AttachmentRef(
                filename=filename,
                mime_type=str(mime_type) if mime_type else None,
                size_bytes=size_bytes,
            )
        )
    return refs


def _attachment_refs_for_row(row) -> list[_AttachmentRef]:
    """Список вложений: из email_attachments, иначе метаданные из raw_payload_json."""
    db_atts = list(row.attachments or [])
    if db_atts:
        return [
            _AttachmentRef(
                filename=att.filename or f"attachment-{index}",
                mime_type=att.mime_type,
                size_bytes=att.size_bytes,
            )
            for index, att in enumerate(db_atts)
        ]
    return _payload_attachment_refs(row.raw_payload_json)


def fetch_attachment_for_download(
    row_id: uuid.UUID,
    index: int,
    *,
    vault: VaultClient | None = None,
) -> AttachmentDownloadResult:
    """Загружает байты вложения по индексу через IMAP (лимит MAX_ATTACHMENT_MB не применяется)."""
    from agent_pochta.workers.runtime import get_worker_container

    factory = get_session_factory()
    container = get_worker_container() if vault is None else None
    vault = vault or (container.vault if container else VaultClient())

    with factory() as session:
        repo = EmailRepository(session)
        row = repo.get_by_id(row_id)
        if row is None:
            return AttachmentDownloadResult(ok=False, row_id=str(row_id), reason="not_found")

        attachments = _attachment_refs_for_row(row)
        if index < 0 or index >= len(attachments):
            return AttachmentDownloadResult(
                ok=False,
                row_id=str(row_id),
                reason="attachment_not_found",
            )

        target = attachments[index]
        filename = target.filename or f"attachment-{index}"
        mime_type = target.mime_type or "application/octet-stream"

        if not row.mailbox:
            return AttachmentDownloadResult(
                ok=False,
                row_id=str(row_id),
                filename=filename,
                mime_type=mime_type,
                reason="no_mailbox",
            )

        mailbox = row.mailbox
        imap_message_id = resolve_imap_message_id(row)

    cache_key = attachment_cache_key(mailbox, imap_message_id, index, filename)
    cached = get_cached_attachment(cache_key)
    if cached is not None:
        logger.info(
            "attachment_cache_hit",
            row_id=str(row_id),
            filename=cached.filename,
            size_bytes=len(cached.content),
        )
        return AttachmentDownloadResult(
            ok=True,
            row_id=str(row_id),
            filename=cached.filename,
            mime_type=cached.mime_type,
            content=cached.content,
        )

    try:
        settings = get_settings()
        credentials = resolve_imap_credentials(mailbox, vault)
        client = ImapMailboxClient(mailbox, credentials, settings=settings)
        fetched = client.fetch_attachment_bytes(
            imap_message_id,
            filename=filename,
            attachment_index=index,
            timeout_sec=settings.imap_download_timeout_sec,
        )
    except Exception as exc:
        logger.warning(
            "attachment_download_imap_failed",
            row_id=str(row_id),
            filename=filename,
            mailbox=mailbox,
            error=str(exc),
        )
        return AttachmentDownloadResult(
            ok=False,
            row_id=str(row_id),
            filename=filename,
            mime_type=mime_type,
            reason=f"imap_error: {exc}",
        )

    if fetched is None:
        logger.info(
            "attachment_download_not_in_mailbox",
            row_id=str(row_id),
            filename=filename,
            mailbox=mailbox,
        )
        return AttachmentDownloadResult(
            ok=False,
            row_id=str(row_id),
            filename=filename,
            mime_type=mime_type,
            reason="not_in_mailbox",
        )

    content, resolved_mime, resolved_name = fetched
    if not content:
        logger.warning(
            "attachment_download_no_content",
            row_id=str(row_id),
            filename=filename,
            mailbox=mailbox,
        )
        return AttachmentDownloadResult(
            ok=False,
            row_id=str(row_id),
            filename=filename,
            mime_type=mime_type,
            reason="attachment_unavailable",
        )

    put_cached_attachment(
        cache_key,
        content=content,
        mime_type=resolved_mime or mime_type,
        filename=resolved_name or filename,
    )
    logger.info(
        "attachment_imap_fetch_restored",
        filename=resolved_name or filename,
        size_bytes=len(content),
        row_id=str(row_id),
    )
    return AttachmentDownloadResult(
        ok=True,
        row_id=str(row_id),
        filename=resolved_name or filename,
        mime_type=resolved_mime or mime_type,
        content=content,
    )
