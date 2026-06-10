from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

from redis import Redis

from app.core.config import settings
from app.models.enums import KnowledgeBaseIndexJobStatus, KnowledgeBaseStatus
from app.models.knowledge_base import KnowledgeBase, KnowledgeBaseIndexingJob


def indexing_channel(knowledge_base_id: uuid.UUID) -> str:
    return f"kb_indexing:{knowledge_base_id}"


def publish_indexing_event(knowledge_base_id: uuid.UUID, payload: dict[str, Any]) -> None:
    client = Redis.from_url(settings.REDIS_URL, decode_responses=True)
    try:
        client.publish(indexing_channel(knowledge_base_id), json.dumps(payload, default=_json_default))
    finally:
        client.close()


def build_indexing_payload(
    *,
    event: str,
    knowledge_base: KnowledgeBase,
    job: KnowledgeBaseIndexingJob | None,
    indexing_active: bool,
) -> dict[str, Any]:
    return {
        "event": event,
        "knowledge_base_id": str(knowledge_base.id),
        "knowledge_base_status": getattr(knowledge_base.status, "value", knowledge_base.status),
        "indexing_active": indexing_active,
        "fragments_count": knowledge_base.fragments_count,
        "sources_count": knowledge_base.sources_count,
        "job": _job_payload(job) if job is not None else None,
        "sent_at": datetime.utcnow().isoformat() + "Z",
    }


def is_indexing_active(
    knowledge_base: KnowledgeBase,
    job: KnowledgeBaseIndexingJob | None = None,
) -> bool:
    if knowledge_base.status in {KnowledgeBaseStatus.PROCESSING, KnowledgeBaseStatus.UPDATING}:
        return True
    if job is not None and job.status in {
        KnowledgeBaseIndexJobStatus.QUEUED,
        KnowledgeBaseIndexJobStatus.RUNNING,
    }:
        return True
    return False


def _job_payload(job: KnowledgeBaseIndexingJob) -> dict[str, Any]:
    processing_params = job.processing_params or {}
    return {
        "id": str(job.id),
        "status": getattr(job.status, "value", job.status),
        "job_type": getattr(job.job_type, "value", job.job_type),
        "processing_params": processing_params or None,
        "current_stage": processing_params.get("current_stage"),
        "cancel_requested": job.cancel_requested,
        "processed_sources_count": job.processed_sources_count,
        "total_sources_count": job.total_sources_count,
        "created_fragments_count": job.created_fragments_count,
        "updated_fragments_count": job.updated_fragments_count,
        "total_chunks_count": job.total_chunks_count,
        "extracted_sources_count": job.extracted_sources_count,
        "chunked_sources_count": job.chunked_sources_count,
        "embedded_chunks_count": job.embedded_chunks_count,
        "qdrant_points_count": job.qdrant_points_count,
        "fulltext_chunks_count": job.fulltext_chunks_count,
        "errors_count": job.errors_count,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
    }


def _json_default(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)
