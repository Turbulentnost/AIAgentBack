"""Разрешение текста письма для BGE по документу 1С / Postgres / IMAP."""

from __future__ import annotations

import logging
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from agent_pochta.config import Settings, get_settings
from agent_pochta.db.message_filters import load_payload_dict, resolved_turbo_recipient
from agent_pochta.db.models import EmailMessageRow
from agent_pochta.db.repository import EmailRepository
from agent_pochta.imap.client import ImapMailboxClient, resolve_imap_credentials
from agent_pochta.services.department_knowledge import build_unified_embed_text
from agent_pochta.services.email_indexing import reextract_full_embedding_text
from agent_pochta.services.onec_routing_corpus import doc_number, doc_ref_key
from agent_pochta.services.odata_attached_file import (
    load_attached_file_field_map,
    read_attached_file_storage_bytes,
)
from agent_pochta.services.odata_client import ODataClient
from agent_pochta.services.vault import VaultClient

logger = logging.getLogger(__name__)

ATTACHED_FILES_ENTITY = "Catalog_ТД_ВходящаяКорреспонденцияПрисоединенныеФайлы"


@dataclass
class ResolvedEmailContent:
    embed_text: str
    recipient: str
    sender_email: str
    subject: str
    row: EmailMessageRow | None
    message_id: str | None
    resolution_source: str
    meta: dict[str, Any]


def _normalize_recipient(value: str | None) -> str:
    return (value or "").strip().lower()


def _embed_from_email_message(email, *, summary: str | None = None) -> str:
    blocks = [
        (att.filename or "file", att.mime_type or "", att.extracted_text or "")
        for att in (email.attachments or [])
    ]
    return build_unified_embed_text(
        subject=email.subject,
        sender_email=email.sender_email,
        summary_ru=summary,
        body_text=email.body_text,
        attachment_blocks=blocks,
    )


def _parse_msg_bytes(content: bytes, mailbox: str) -> tuple[str, str, str] | None:
    if len(content) < 8:
        return None
    try:
        from aspose.email_foss import msg as msgmod
    except ImportError:
        return None
    with tempfile.NamedTemporaryFile(suffix=".msg", delete=False) as tmp:
        path = Path(tmp.name)
        path.write_bytes(content)
    try:
        message = msgmod.MapiMessage.from_file(str(path))
        subject = str(getattr(message, "subject", "") or "")
        body = str(getattr(message, "body", "") or getattr(message, "body_html", "") or "")
        sender = str(
            getattr(message, "sender_email_address", None)
            or getattr(message, "sender_name", None)
            or ""
        )
        text = build_unified_embed_text(
            subject=subject,
            sender_email=sender,
            body_text=body,
        )
        return text, sender, subject
    except Exception as exc:
        logger.warning("msg_parse_failed: %s", exc)
        return None
    finally:
        path.unlink(missing_ok=True)


def _fetch_msg_from_1c(
    doc: dict[str, Any],
    *,
    client: ODataClient,
    field_map: dict[str, str],
) -> tuple[bytes, str] | None:
    ref = doc_ref_key(doc)
    number = doc_number(doc)
    if not ref:
        return None
    filter_expr = f"ВладелецФайла_Key eq guid'{ref}'"
    items = client.fetch_filtered(ATTACHED_FILES_ENTITY, filter_expr=filter_expr, page_size=50)
    candidates = []
    for item in items:
        ext = str(item.get("Расширение") or "").lower()
        desc = str(item.get("Description") or "")
        if ext != "msg":
            continue
        if number and desc == number:
            candidates.insert(0, item)
        else:
            candidates.append(item)
    if not candidates:
        return None
    item = candidates[0]
    file_ref = str(item.get("Ref_Key") or "")
    if not file_ref:
        return None
    content = read_attached_file_storage_bytes(
        client,
        entity=ATTACHED_FILES_ENTITY,
        ref_key=file_ref,
        field_map=field_map,
    )
    return content, str(item.get("Description") or number or "letter.msg")


def find_row_for_doc(session: Session, doc: dict[str, Any]) -> EmailMessageRow | None:
    number = doc_number(doc)
    ref = doc_ref_key(doc)
    stmt = select(EmailMessageRow).options(selectinload(EmailMessageRow.attachments))
    if ref and number:
        stmt = stmt.where(
            or_(
                EmailMessageRow.erp_task_id == ref,
                EmailMessageRow.erp_document_number == number,
            )
        )
    elif number:
        stmt = stmt.where(EmailMessageRow.erp_document_number == number)
    elif ref:
        stmt = stmt.where(EmailMessageRow.erp_task_id == ref)
    else:
        return None
    return session.scalars(stmt.order_by(EmailMessageRow.received_at.desc()).limit(1)).first()


def resolve_email_for_doc(
    doc: dict[str, Any],
    *,
    session: Session,
    settings: Settings | None = None,
    reextract: bool = False,
    vault: VaultClient | None = None,
    odata_client: ODataClient | None = None,
) -> ResolvedEmailContent:
    settings = settings or get_settings()
    meta: dict[str, Any] = {"doc_number": doc_number(doc), "doc_ref": doc_ref_key(doc)}

    recipient = _normalize_recipient(
        doc.get("EmailПолучателяПисьма") or doc.get("EmailПолучатель") or ""
    )
    sender = _normalize_recipient(doc.get("EmailОтправителяПисьма") or "")
    subject = str(doc.get("ТемаСлужебнойЗаписки") or doc.get("Содержание") or "")[:500]

    row = find_row_for_doc(session, doc)
    if row is not None:
        payload = load_payload_dict(row.raw_payload_json) or {}
        row_recipient = resolved_turbo_recipient(mailbox=row.mailbox, payload=payload)
        if row_recipient:
            recipient = row_recipient
        if not sender:
            sender = (row.sender_email or "").strip().lower()
        if not subject:
            subject = row.subject or ""

        stored = str(payload.get("embedding_source_text") or "").strip()
        if reextract and len(stored) < settings.email_rag_min_chars:
            repo = EmailRepository(session)
            result = reextract_full_embedding_text(repo, row, vault=vault)
            meta["reextract"] = result
            payload = load_payload_dict(row.raw_payload_json) or {}
            stored = str(payload.get("embedding_source_text") or "").strip()

        if stored and len(stored) >= settings.email_rag_min_chars:
            return ResolvedEmailContent(
                embed_text=stored,
                recipient=recipient or row.mailbox or "",
                sender_email=sender or row.sender_email or "",
                subject=subject or row.subject or "",
                row=row,
                message_id=row.message_id,
                resolution_source="postgres_embedding",
                meta=meta,
            )

        from agent_pochta.services.department_knowledge import build_unified_embed_text_from_row

        text = build_unified_embed_text_from_row(row).strip()
        if len(text) >= settings.email_rag_min_chars:
            return ResolvedEmailContent(
                embed_text=text,
                recipient=recipient or row.mailbox or "",
                sender_email=sender or row.sender_email or "",
                subject=subject or row.subject or "",
                row=row,
                message_id=row.message_id,
                resolution_source="postgres_row",
                meta=meta,
            )

    message_id = str(doc.get("ID_XML") or "").strip()
    if not message_id and row is not None:
        message_id = row.message_id or ""

    mailbox = recipient or (row.mailbox if row else "")
    if message_id and mailbox:
        try:
            vault = vault or VaultClient()
            creds = resolve_imap_credentials(mailbox, vault)
            imap = ImapMailboxClient(mailbox, creds, settings=settings)
            email = imap.fetch_by_message_id(message_id, mark_seen=False, load_oversized_attachments=True)
            if email is not None:
                text = _embed_from_email_message(email).strip()
                if len(text) >= settings.email_rag_min_chars:
                    return ResolvedEmailContent(
                        embed_text=text,
                        recipient=mailbox,
                        sender_email=email.sender_email or sender,
                        subject=email.subject or subject,
                        row=row,
                        message_id=email.message_id,
                        resolution_source="imap_message_id",
                        meta=meta,
                    )
        except Exception as exc:
            meta["imap_message_id_error"] = str(exc)

    if subject and mailbox:
        try:
            vault = vault or VaultClient()
            creds = resolve_imap_credentials(mailbox, vault)
            imap = ImapMailboxClient(mailbox, creds, settings=settings)
            email = imap.fetch_by_subject(
                subject,
                sender_email=sender or None,
                mark_seen=False,
                load_oversized_attachments=True,
            )
            if email is not None:
                text = _embed_from_email_message(email).strip()
                if len(text) >= settings.email_rag_min_chars:
                    return ResolvedEmailContent(
                        embed_text=text,
                        recipient=mailbox,
                        sender_email=email.sender_email or sender,
                        subject=email.subject or subject,
                        row=row,
                        message_id=email.message_id,
                        resolution_source="imap_subject",
                        meta=meta,
                    )
        except Exception as exc:
            meta["imap_subject_error"] = str(exc)

    if settings.odata_base_url:
        try:
            client = odata_client or ODataClient(
                settings.odata_base_url,
                username=settings.odata_username,
                password=settings.odata_password,
                timeout_sec=120,
            )
            field_map = load_attached_file_field_map()
            fetched = _fetch_msg_from_1c(doc, client=client, field_map=field_map)
            if fetched:
                content, _desc = fetched
                parsed = _parse_msg_bytes(content, mailbox or "unknown")
                if parsed:
                    text, msg_sender, msg_subject = parsed
                    if len(text.strip()) >= settings.email_rag_min_chars:
                        return ResolvedEmailContent(
                            embed_text=text.strip(),
                            recipient=mailbox,
                            sender_email=msg_sender or sender,
                            subject=msg_subject or subject,
                            row=row,
                            message_id=message_id or None,
                            resolution_source="1c_msg_attachment",
                            meta=meta,
                        )
        except Exception as exc:
            meta["1c_msg_error"] = str(exc)

    fallback = build_unified_embed_text(
        subject=subject,
        sender_email=sender,
        body_text=str(doc.get("Содержание") or ""),
    ).strip()
    return ResolvedEmailContent(
        embed_text=fallback,
        recipient=mailbox,
        sender_email=sender,
        subject=subject,
        row=row,
        message_id=message_id or None,
        resolution_source="1c_metadata_fallback",
        meta=meta,
    )
