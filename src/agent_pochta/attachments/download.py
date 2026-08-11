"""On-demand скачивание вложений письма из IMAP (байты в БД не хранятся)."""

from __future__ import annotations

import json
import threading
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
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


@dataclass
class AttachmentStreamResult:
    ok: bool
    row_id: str
    filename: str = ""
    mime_type: str = "application/octet-stream"
    reason: str | None = None
    cached: bool = False
    _chunks: Iterator[bytes] | None = field(default=None, repr=False)

    @property
    def has_content(self) -> bool:
        return self.ok and self._chunks is not None

    def iter_bytes(self) -> Iterator[bytes]:
        if self._chunks is None:
            return iter(())
        return self._chunks


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


def _resolve_vault(vault: VaultClient | None) -> VaultClient:
    if vault is not None:
        return vault
    from agent_pochta.workers.runtime import get_worker_container

    container = get_worker_container()
    return container.vault if container else VaultClient()


def _row_download_context(
    row_id: uuid.UUID,
    index: int,
) -> tuple[str, str, _AttachmentRef] | AttachmentDownloadResult:
    factory = get_session_factory()
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

        return row.mailbox, resolve_imap_message_id(row), target


def stream_attachment_for_download(
    row_id: uuid.UUID,
    index: int,
    *,
    vault: VaultClient | None = None,
) -> AttachmentStreamResult:
    """Стримит вложение: cache hit целиком, иначе IMAP чанками с записью в кэш."""
    ctx = _row_download_context(row_id, index)
    if isinstance(ctx, AttachmentDownloadResult):
        return AttachmentStreamResult(
            ok=False,
            row_id=ctx.row_id,
            filename=ctx.filename,
            mime_type=ctx.mime_type,
            reason=ctx.reason,
        )

    mailbox, imap_message_id, target = ctx
    filename = target.filename or f"attachment-{index}"
    mime_type = target.mime_type or "application/octet-stream"
    cache_key = attachment_cache_key(mailbox, imap_message_id, index, filename)

    cached = get_cached_attachment(cache_key)
    if cached is not None:
        logger.info(
            "attachment_cache_hit",
            row_id=str(row_id),
            filename=cached.filename,
            size_bytes=len(cached.content),
        )

        def _cached_chunks() -> Iterator[bytes]:
            yield cached.content

        return AttachmentStreamResult(
            ok=True,
            row_id=str(row_id),
            filename=cached.filename,
            mime_type=cached.mime_type,
            cached=True,
            _chunks=_cached_chunks(),
        )

    vault = _resolve_vault(vault)
    settings = get_settings()

    try:
        credentials = resolve_imap_credentials(mailbox, vault)
        client = ImapMailboxClient(mailbox, credentials, settings=settings)
        # Один проход: peek первого чанка (ошибка/not_found до HTTP 200), далее стрим.
        peek_iter = client.iter_attachment_chunks(
            imap_message_id,
            filename=filename,
            attachment_index=index,
            timeout_sec=settings.imap_download_timeout_sec,
        )
        first = next(peek_iter)
    except StopIteration:
        logger.info(
            "attachment_download_not_in_mailbox",
            row_id=str(row_id),
            filename=filename,
            mailbox=mailbox,
        )
        return AttachmentStreamResult(
            ok=False,
            row_id=str(row_id),
            filename=filename,
            mime_type=mime_type,
            reason="not_in_mailbox",
        )
    except Exception as exc:
        logger.warning(
            "attachment_download_imap_failed",
            row_id=str(row_id),
            filename=filename,
            mailbox=mailbox,
            error=str(exc),
        )
        return AttachmentStreamResult(
            ok=False,
            row_id=str(row_id),
            filename=filename,
            mime_type=mime_type,
            reason=f"imap_error: {exc}",
        )

    first_chunk, first_mime, first_name = first
    resolved_mime = first_mime or mime_type
    resolved_name = first_name or filename

    def _stream_from_peek() -> Iterator[bytes]:
        buffer = bytearray(first_chunk)
        yield first_chunk
        try:
            for chunk, meta_mime, meta_name in peek_iter:
                nonlocal resolved_mime, resolved_name
                resolved_mime = meta_mime or resolved_mime
                resolved_name = meta_name or resolved_name
                buffer.extend(chunk)
                yield chunk
        finally:
            if buffer:
                put_cached_attachment(
                    cache_key,
                    content=bytes(buffer),
                    mime_type=resolved_mime,
                    filename=resolved_name,
                )
                logger.info(
                    "attachment_imap_fetch_restored",
                    filename=resolved_name,
                    size_bytes=len(buffer),
                    row_id=str(row_id),
                )

    return AttachmentStreamResult(
        ok=True,
        row_id=str(row_id),
        filename=resolved_name,
        mime_type=resolved_mime,
        _chunks=_stream_from_peek(),
    )


def fetch_attachment_for_download(
    row_id: uuid.UUID,
    index: int,
    *,
    vault: VaultClient | None = None,
) -> AttachmentDownloadResult:
    """Загружает байты вложения по индексу через IMAP (лимит MAX_ATTACHMENT_MB не применяется)."""
    streamed = stream_attachment_for_download(row_id, index, vault=vault)
    if not streamed.ok:
        return AttachmentDownloadResult(
            ok=False,
            row_id=streamed.row_id,
            filename=streamed.filename,
            mime_type=streamed.mime_type,
            reason=streamed.reason,
        )
    try:
        content = b"".join(streamed.iter_bytes())
    except Exception as exc:
        return AttachmentDownloadResult(
            ok=False,
            row_id=str(row_id),
            filename=streamed.filename,
            mime_type=streamed.mime_type,
            reason=f"imap_error: {exc}",
        )
    if not content:
        return AttachmentDownloadResult(
            ok=False,
            row_id=str(row_id),
            filename=streamed.filename,
            mime_type=streamed.mime_type,
            reason="attachment_unavailable",
        )
    return AttachmentDownloadResult(
        ok=True,
        row_id=str(row_id),
        filename=streamed.filename,
        mime_type=streamed.mime_type,
        content=content,
    )


def prefetch_attachments_for_row(
    row_id: uuid.UUID,
    *,
    vault: VaultClient | None = None,
) -> dict[str, int]:
    """Одним IMAP-соединением прогревает кэш всех вложений письма."""
    factory = get_session_factory()
    with factory() as session:
        repo = EmailRepository(session)
        row = repo.get_by_id(row_id)
        if row is None or not row.mailbox:
            return {"cached": 0, "fetched": 0, "failed": 0}
        attachments = _attachment_refs_for_row(row)
        mailbox = row.mailbox
        imap_message_id = resolve_imap_message_id(row)

    if not attachments:
        return {"cached": 0, "fetched": 0, "failed": 0}

    settings = get_settings()
    vault = _resolve_vault(vault)
    to_fetch: list[tuple[int, str]] = []
    cached = 0
    for index, ref in enumerate(attachments):
        filename = ref.filename or f"attachment-{index}"
        key = attachment_cache_key(mailbox, imap_message_id, index, filename)
        if get_cached_attachment(key) is not None:
            cached += 1
        else:
            to_fetch.append((index, filename))

    if not to_fetch:
        return {"cached": cached, "fetched": 0, "failed": 0}

    try:
        credentials = resolve_imap_credentials(mailbox, vault)
        client = ImapMailboxClient(mailbox, credentials, settings=settings)
        fetched_map = client.fetch_attachments_batch(
            imap_message_id,
            to_fetch,
            timeout_sec=settings.imap_download_timeout_sec,
        )
    except Exception as exc:
        logger.warning(
            "attachment_prefetch_failed",
            row_id=str(row_id),
            mailbox=mailbox,
            error=str(exc),
        )
        return {"cached": cached, "fetched": 0, "failed": len(to_fetch)}

    fetched = 0
    for index, filename in to_fetch:
        item = fetched_map.get(index)
        if not item:
            continue
        content, mime_type, resolved_name = item
        if not content:
            continue
        put_cached_attachment(
            attachment_cache_key(mailbox, imap_message_id, index, filename),
            content=content,
            mime_type=mime_type,
            filename=resolved_name or filename,
        )
        fetched += 1

    failed = len(to_fetch) - fetched
    logger.info(
        "attachment_prefetch_done",
        row_id=str(row_id),
        cached=cached,
        fetched=fetched,
        failed=failed,
    )
    return {"cached": cached, "fetched": fetched, "failed": failed}


def schedule_attachment_prefetch(row_id: uuid.UUID, *, vault: VaultClient | None = None) -> None:
    """Фоновый prefetch после открытия тела письма (не блокирует UI)."""
    settings = get_settings()
    if not getattr(settings, "attachment_prefetch_on_body", True):
        return

    def _run() -> None:
        try:
            prefetch_attachments_for_row(row_id, vault=vault)
        except Exception as exc:
            logger.warning("attachment_prefetch_thread_failed", row_id=str(row_id), error=str(exc))

    threading.Thread(
        target=_run,
        name=f"att-prefetch-{str(row_id)[:8]}",
        daemon=True,
    ).start()
