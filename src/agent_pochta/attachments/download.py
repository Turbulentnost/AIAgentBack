"""On-demand скачивание вложений письма из IMAP (байты в БД не хранятся)."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from urllib.parse import quote

import structlog

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

    try:
        settings = get_settings()
        credentials = resolve_imap_credentials(mailbox, vault)
        client = ImapMailboxClient(mailbox, credentials, settings=settings)
        fresh = client.fetch_by_message_id(
            imap_message_id,
            load_oversized_attachments=True,
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

    if fresh is None:
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

    by_name = {a.filename: a for a in fresh.attachments if a.filename}
    matched = by_name.get(filename)
    if matched is None and 0 <= index < len(fresh.attachments):
        matched = fresh.attachments[index]

    if matched is None or not matched.content:
        logger.warning(
            "attachment_download_no_content",
            row_id=str(row_id),
            filename=filename,
            imap_files=[a.filename for a in fresh.attachments],
            imap_sizes=[a.size_bytes for a in fresh.attachments],
        )
        return AttachmentDownloadResult(
            ok=False,
            row_id=str(row_id),
            filename=filename,
            mime_type=mime_type,
            reason="attachment_unavailable",
        )

    logger.info(
        "attachment_imap_fetch_restored",
        filename=matched.filename or filename,
        size_bytes=len(matched.content),
        row_id=str(row_id),
    )
    return AttachmentDownloadResult(
        ok=True,
        row_id=str(row_id),
        filename=matched.filename or filename,
        mime_type=matched.mime_type or mime_type,
        content=matched.content,
    )
