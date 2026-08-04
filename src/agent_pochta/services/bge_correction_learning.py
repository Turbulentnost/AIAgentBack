"""Realtime BGE learning: operator correction → department_corrections_bge."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from agent_pochta.config import Settings, get_settings
from agent_pochta.db.message_filters import load_payload_dict, resolved_turbo_recipient
from agent_pochta.db.models import EmailMessageRow
from agent_pochta.db.repository import EmailRepository
from agent_pochta.services.department_correction_indexing import sync_department_correction_records
from agent_pochta.services.department_knowledge import (
    DepartmentCorrectionRecord,
    build_unified_embed_text_from_row,
    dept_name,
    load_department_display_names,
)
from agent_pochta.services.email_indexing import reextract_full_embedding_text

logger = logging.getLogger(__name__)


def _fingerprint(*, sender: str, recipient: str, subject: str) -> str:
    subj = " ".join((subject or "").lower().split())[:120]
    return f"{sender.lower().strip()}|{recipient.lower().strip()}|{subj}"


def build_correction_record(
    row: EmailMessageRow | None,
    *,
    wrong_dept_id: str,
    wrong_dept_name: str,
    correct_dept_id: str,
    correct_dept_name: str,
    embed_text: str,
    recipient: str = "",
    sender_email: str = "",
    subject: str = "",
    message_id: str | None = None,
    email_id: str | None = None,
    source: str = "operator_change",
) -> DepartmentCorrectionRecord:
    if row is not None:
        payload = load_payload_dict(row.raw_payload_json) or {}
        recipient = recipient or resolved_turbo_recipient(mailbox=row.mailbox, payload=payload)
        if not recipient:
            recipient = str(payload.get("routing_recipient") or row.mailbox or "")
        sender_email = sender_email or row.sender_email or ""
        subject = subject or row.subject or ""
        message_id = message_id or row.message_id
        email_id = email_id or str(row.id)
    names = load_department_display_names()
    return DepartmentCorrectionRecord(
        source=source,
        email_id=email_id,
        message_id=message_id,
        recipient=recipient,
        sender_email=sender_email,
        subject=subject,
        embed_text=embed_text,
        dept_wrong_id=wrong_dept_id,
        dept_wrong_name=dept_name(names, wrong_dept_id, wrong_dept_name),
        dept_correct_id=correct_dept_id,
        dept_correct_name=dept_name(names, correct_dept_id, correct_dept_name),
        fingerprint=_fingerprint(
            sender=sender_email,
            recipient=recipient,
            subject=subject,
        ),
    )


def resolve_embed_text_for_row(
    repo: EmailRepository,
    row: EmailMessageRow,
    *,
    reextract: bool = True,
) -> tuple[str, dict[str, Any]]:
    meta: dict[str, Any] = {"reextract": reextract}
    if reextract:
        result = reextract_full_embedding_text(repo, row)
        meta["reextract_result"] = result
        if result.get("ok"):
            payload = load_payload_dict(row.raw_payload_json) or {}
            stored = str(payload.get("embedding_source_text") or "").strip()
            if stored:
                return stored, meta
    text = build_unified_embed_text_from_row(row).strip()
    return text, meta


def upsert_correction_from_row(
    repo: EmailRepository,
    row: EmailMessageRow,
    *,
    wrong_dept_id: str | None,
    wrong_dept_name: str | None,
    correct_dept_id: str,
    correct_dept_name: str,
    reextract: bool = True,
    settings: Settings | None = None,
) -> dict[str, Any]:
    settings = settings or get_settings()
    wrong_id = (wrong_dept_id or "").strip()
    correct_id = (correct_dept_id or "").strip()
    if not correct_id:
        return {"ok": False, "skipped": True, "reason": "no_correct_dept"}
    if wrong_id and wrong_id == correct_id:
        return {"ok": False, "skipped": True, "reason": "dept_unchanged"}

    embed_text, embed_meta = resolve_embed_text_for_row(repo, row, reextract=reextract)
    if len(embed_text.strip()) < settings.email_rag_min_chars:
        return {"ok": False, "skipped": True, "reason": "text_too_short", **embed_meta}

    record = build_correction_record(
        row,
        wrong_dept_id=wrong_id,
        wrong_dept_name=wrong_dept_name or "",
        correct_dept_id=correct_id,
        correct_dept_name=correct_dept_name or "",
        embed_text=embed_text,
        source="operator_change",
    )
    result = sync_department_correction_records([record], settings=settings)
    payload = load_payload_dict(row.raw_payload_json) or {}
    payload["bge_correction_indexed_at"] = (
        datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    )
    row.raw_payload_json = json.dumps(payload, ensure_ascii=False)
    return {**result, **embed_meta, "record_id": record.email_id}


def upsert_correction_by_email_id(
    email_id: uuid.UUID | str,
    *,
    wrong_dept_id: str | None,
    wrong_dept_name: str | None,
    correct_dept_id: str,
    correct_dept_name: str,
    reextract: bool = True,
    session_factory=None,
) -> dict[str, Any]:
    from agent_pochta.db.session import get_session_factory

    factory = session_factory or get_session_factory()
    with factory() as session:
        repo = EmailRepository(session)
        row = session.scalar(
            select(EmailMessageRow)
            .where(EmailMessageRow.id == uuid.UUID(str(email_id)))
            .options(selectinload(EmailMessageRow.attachments))
        )
        if row is None:
            return {"ok": False, "reason": "not_found"}
        result = upsert_correction_from_row(
            repo,
            row,
            wrong_dept_id=wrong_dept_id,
            wrong_dept_name=wrong_dept_name,
            correct_dept_id=correct_dept_id,
            correct_dept_name=correct_dept_name,
            reextract=reextract,
        )
        if result.get("ok"):
            session.commit()
        return result


def upsert_correction_from_1c_oracle(
    *,
    embed_text: str,
    recipient: str,
    sender_email: str,
    subject: str,
    wrong_dept_id: str | None,
    wrong_dept_name: str | None,
    correct_dept_id: str,
    correct_dept_name: str,
    row: EmailMessageRow | None = None,
    message_id: str | None = None,
    email_id: str | None = None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Upsert BGE correction from 1C oracle (automated train loop)."""
    settings = settings or get_settings()
    wrong_id = (wrong_dept_id or "").strip()
    correct_id = (correct_dept_id or "").strip()
    text = (embed_text or "").strip()
    if not correct_id:
        return {"ok": False, "skipped": True, "reason": "no_correct_dept"}
    if wrong_id and wrong_id == correct_id:
        return {"ok": False, "skipped": True, "reason": "dept_unchanged"}
    if len(text) < settings.email_rag_min_chars:
        return {"ok": False, "skipped": True, "reason": "text_too_short"}

    record = build_correction_record(
        row,
        wrong_dept_id=wrong_id,
        wrong_dept_name=wrong_dept_name or "",
        correct_dept_id=correct_id,
        correct_dept_name=correct_dept_name or "",
        embed_text=text,
        recipient=recipient,
        sender_email=sender_email,
        subject=subject,
        message_id=message_id,
        email_id=str(row.id) if row is not None else email_id,
        source="1c_oracle_train",
    )
    return sync_department_correction_records([record], settings=settings)
