"""Индексация писем (тело + вложения) в Qdrant через BGE."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from agent_pochta.config import Settings, get_settings
from agent_pochta.db.models import EmailMessageRow
from agent_pochta.db.repository import EmailRepository
from agent_pochta.services.department_knowledge import (
    build_unified_embed_text,
    build_unified_embed_text_from_row,
)
from agent_pochta.services.embedding_client import EmbeddingClientError, embed_texts
from agent_pochta.services.email_rag_qdrant import upsert_email_chunks
from agent_pochta.state import AgentState

logger = logging.getLogger(__name__)


def chunk_text(text: str, *, max_chars: int, overlap: int) -> list[str]:
    normalized = text.strip()
    if not normalized:
        return []
    if len(normalized) <= max_chars:
        return [normalized]

    chunks: list[str] = []
    start = 0
    while start < len(normalized):
        end = min(start + max_chars, len(normalized))
        piece = normalized[start:end].strip()
        if piece:
            chunks.append(piece)
        if end >= len(normalized):
            break
        start = max(end - overlap, start + 1)
    return chunks


def build_indexing_text_from_state(state: AgentState) -> str:
    email = state["email"]
    combined = (state.get("combined_text") or "").strip()
    if combined:
        return combined
    attachment_blocks = [
        (att.filename or "file", att.mime_type or "", att.extracted_text or "")
        for att in email.attachments
    ]
    return build_unified_embed_text(
        subject=email.subject,
        sender_email=email.sender_email,
        summary_ru=state.get("summary_ru"),
        body_text=email.body_text,
        attachment_blocks=attachment_blocks,
    )


def build_indexing_text_from_row(row: EmailMessageRow) -> str:
    return build_unified_embed_text_from_row(row)


def reextract_full_embedding_text(
    repo: EmailRepository,
    row: EmailMessageRow,
    *,
    vault=None,
    documents=None,
) -> dict[str, Any]:
    """Подтягивает тело и вложения из IMAP и собирает полный combined_text (как узел 4)."""
    from agent_pochta.attachments.imap_fetch import ensure_attachments_from_imap
    from agent_pochta.attachments.pipeline import process_email_attachments
    from agent_pochta.imap.body_fetch import fetch_and_cache_email_body, row_has_cached_body
    from agent_pochta.workers.runtime import get_worker_container

    settings = get_settings()
    container = get_worker_container() if vault is None and documents is None else None
    vault = vault or (container.vault if container else None)
    documents = documents or (container.documents if container else None)
    if vault is None or documents is None:
        return {"ok": False, "error": "services_unavailable"}

    body_fetched = False
    if not row_has_cached_body(row):
        fetch_result = fetch_and_cache_email_body(row.id, vault=vault)
        body_fetched = fetch_result.ok and not fetch_result.cached
        if not fetch_result.ok:
            logger.warning(
                "reextract_body_fetch_failed email_id=%s reason=%s",
                row.id,
                fetch_result.reason,
            )
        repo._session.refresh(row)

    email = repo.load_email_from_row(row)
    if email is None:
        return {"ok": False, "error": "no_email_payload"}

    attachments_restored = ensure_attachments_from_imap(
        email, vault, load_oversized=True
    )
    result = process_email_attachments(email, documents)
    combined = (result.combined_text or "").strip()
    if not combined:
        return {"ok": False, "error": "empty_combined_text"}

    payload: dict[str, Any] = {}
    if row.raw_payload_json:
        try:
            raw = json.loads(row.raw_payload_json)
            if isinstance(raw, dict):
                payload = raw
        except json.JSONDecodeError:
            payload = {}
    payload["embedding_source_text"] = combined[: settings.email_rag_max_source_chars]
    payload["embedding_reextracted_at"] = (
        datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    )
    row.raw_payload_json = json.dumps(payload, ensure_ascii=False)

    for att in email.attachments:
        if not att.filename or not att.extracted_text:
            continue
        for db_att in row.attachments or []:
            if db_att.filename == att.filename:
                db_att.extracted_text = att.extracted_text[: settings.document_storage_excerpt_chars]
                db_att.ocr_used = bool(att.ocr_used)
                break

    repo._session.flush()
    return {
        "ok": True,
        "text_len": len(combined),
        "body_fetched": body_fetched,
        "attachments_restored": attachments_restored,
    }


def _chunk_payloads(row: EmailMessageRow, texts: list[str]) -> list[dict[str, Any]]:
    filenames = [att.filename for att in (row.attachments or []) if att.filename]
    received = row.received_at.isoformat(sep=" ") if row.received_at else None
    return [
        {
            "chunk_index": index,
            "chunk_text": chunk,
            "message_id": row.message_id,
            "mailbox": row.mailbox,
            "subject": row.subject,
            "status": row.status,
            "department_id": row.department_id,
            "received_at": received,
            "attachment_filenames": filenames,
            "has_attachments": bool(filenames),
        }
        for index, chunk in enumerate(texts)
    ]


def index_email_row(
    repo: EmailRepository,
    row: EmailMessageRow,
    *,
    settings: Settings | None = None,
    force: bool = False,
    reextract: bool = False,
) -> dict[str, Any]:
    settings = settings or get_settings()
    if not settings.email_rag_enabled:
        return {"ok": False, "skipped": True, "reason": "disabled"}
    if settings.rag_backend != "qdrant":
        return {"ok": False, "skipped": True, "reason": "stub_backend"}
    if not settings.embedding_base_url:
        return {"ok": False, "skipped": True, "reason": "no_embedding_url"}

    payload: dict[str, Any] = {}
    if row.raw_payload_json:
        try:
            raw = json.loads(row.raw_payload_json)
            if isinstance(raw, dict):
                payload = raw
        except json.JSONDecodeError:
            payload = {}

    if not force and payload.get("qdrant_indexed_at"):
        return {"ok": True, "skipped": True, "reason": "already_indexed"}

    reextract_meta: dict[str, Any] | None = None
    if reextract:
        reextract_meta = reextract_full_embedding_text(repo, row)
        if not reextract_meta.get("ok"):
            return {
                "ok": False,
                "skipped": True,
                "reason": reextract_meta.get("error", "reextract_failed"),
            }
        payload = json.loads(row.raw_payload_json or "{}")

    source_text = build_indexing_text_from_row(row)
    if len(source_text.strip()) < settings.email_rag_min_chars:
        return {"ok": False, "skipped": True, "reason": "text_too_short"}

    chunks = chunk_text(
        source_text,
        max_chars=settings.email_rag_chunk_chars,
        overlap=settings.email_rag_chunk_overlap,
    )
    if not chunks:
        return {"ok": False, "skipped": True, "reason": "no_chunks"}

    try:
        vectors = embed_texts(chunks, settings=settings)
    except EmbeddingClientError as exc:
        logger.warning("email_index_embedding_failed email_id=%s error=%s", row.id, exc)
        return {"ok": False, "error": str(exc)}

    email_id = str(row.id)
    upserted = upsert_email_chunks(
        url=settings.qdrant_url,
        email_id=email_id,
        chunks=_chunk_payloads(row, chunks),
        vectors=vectors,
        settings=settings,
    )

    payload["qdrant_indexed_at"] = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    payload["qdrant_chunk_count"] = upserted
    if reextract or not payload.get("embedding_source_text"):
        payload["embedding_source_text"] = source_text[: settings.email_rag_max_source_chars]
    row.raw_payload_json = json.dumps(payload, ensure_ascii=False)
    result: dict[str, Any] = {"ok": True, "email_id": email_id, "chunks": upserted}
    if reextract_meta:
        result["reextract"] = reextract_meta
    return result


def index_email_by_id(
    row_id: uuid.UUID | str,
    *,
    force: bool = False,
    session_factory=None,
) -> dict[str, Any]:
    from agent_pochta.db.session import get_session_factory

    factory = session_factory or get_session_factory()
    with factory() as session:
        repo = EmailRepository(session)
        row = repo.get_by_id(uuid.UUID(str(row_id)))
        if row is None:
            return {"ok": False, "reason": "not_found"}
        result = index_email_row(repo, row, force=force)
        if result.get("ok"):
            session.commit()
        return result
