"""Sync department correction records to Qdrant via BGE."""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from typing import Any

from agent_pochta.config import Settings, get_settings
from agent_pochta.services.department_knowledge import DepartmentCorrectionRecord
from agent_pochta.services.embedding_client import EmbeddingClientError, embed_texts
from agent_pochta.services.email_rag_qdrant import upsert_department_correction

logger = logging.getLogger(__name__)


def record_id_for(record: DepartmentCorrectionRecord) -> str:
    if record.email_id:
        return f"email:{record.email_id}"
    digest = hashlib.sha256(record.fingerprint.encode("utf-8")).hexdigest()[:32]
    return f"corr:{digest}"


def sync_department_correction_records(
    records: list[DepartmentCorrectionRecord],
    *,
    settings: Settings | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    settings = settings or get_settings()
    if not settings.email_rag_enabled or settings.rag_backend != "qdrant":
        return {"ok": False, "skipped": True, "reason": "disabled_or_stub"}
    if not settings.embedding_base_url:
        return {"ok": False, "skipped": True, "reason": "no_embedding_url"}

    indexed = 0
    skipped = 0
    errors = 0
    batch = records[:limit] if limit else records

    for record in batch:
        text = (record.embed_text or "").strip()
        if len(text) < settings.email_rag_min_chars:
            skipped += 1
            continue
        try:
            vectors = embed_texts([text], settings=settings)
        except EmbeddingClientError as exc:
            logger.warning("dept_correction_embed_failed error=%s", exc)
            errors += 1
            continue

        rid = record_id_for(record)
        upsert_department_correction(
            url=settings.qdrant_url,
            record_id=rid,
            embed_text=text,
            vector=vectors[0],
            payload={
                "email_id": record.email_id,
                "message_id": record.message_id,
                "recipient": record.recipient,
                "sender_email": record.sender_email,
                "subject": record.subject,
                "dept_wrong_id": record.dept_wrong_id,
                "dept_wrong_name": record.dept_wrong_name,
                "dept_correct_id": record.dept_correct_id,
                "dept_correct_name": record.dept_correct_name,
                "source": record.source,
                "corrected_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
            },
            settings=settings,
        )
        indexed += 1

    return {
        "ok": True,
        "indexed": indexed,
        "skipped": skipped,
        "errors": errors,
        "total": len(batch),
    }
