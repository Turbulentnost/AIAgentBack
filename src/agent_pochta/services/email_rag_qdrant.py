"""Qdrant-коллекция email_messages: семантический поиск по письмам и вложениям (BGE)."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    PayloadSchemaType,
    PointStruct,
    VectorParams,
)

from agent_pochta.config import Settings, get_settings

logger = logging.getLogger(__name__)

EMAIL_MESSAGES_COLLECTION = "email_messages"
DEPARTMENT_CORRECTIONS_COLLECTION = "department_corrections_bge"


def _correction_point_id(record_id: str, chunk_index: int) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, f"dept_corr:{record_id}:{chunk_index}"))


def _point_id(email_id: str, chunk_index: int) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, f"email:{email_id}:{chunk_index}"))


def ensure_email_messages_collection(
    url: str,
    *,
    vector_size: int | None = None,
) -> None:
    settings = get_settings()
    size = vector_size or settings.embedding_vector_size
    client = QdrantClient(url=url, prefer_grpc=False)
    try:
        existing = {c.name for c in client.get_collections().collections}
        if EMAIL_MESSAGES_COLLECTION not in existing:
            client.create_collection(
                EMAIL_MESSAGES_COLLECTION,
                vectors_config=VectorParams(size=size, distance=Distance.COSINE),
            )
        for field in ("email_id", "message_id", "mailbox", "status"):
            try:
                client.create_payload_index(
                    collection_name=EMAIL_MESSAGES_COLLECTION,
                    field_name=field,
                    field_schema=PayloadSchemaType.KEYWORD,
                )
            except Exception:
                pass
    finally:
        client.close()


def delete_email_points(*, url: str, email_id: str) -> int:
    client = QdrantClient(url=url, prefer_grpc=False)
    deleted = 0
    try:
        while True:
            points, next_offset = client.scroll(
                collection_name=EMAIL_MESSAGES_COLLECTION,
                scroll_filter=Filter(
                    must=[FieldCondition(key="email_id", match=MatchValue(value=email_id))]
                ),
                limit=256,
                with_payload=False,
                with_vectors=False,
            )
            if not points:
                break
            ids = [point.id for point in points]
            client.delete(collection_name=EMAIL_MESSAGES_COLLECTION, points_selector=ids)
            deleted += len(ids)
            if next_offset is None:
                break
    finally:
        client.close()
    return deleted


def upsert_email_chunks(
    *,
    url: str,
    email_id: str,
    chunks: list[dict[str, Any]],
    vectors: list[list[float]],
    settings: Settings | None = None,
) -> int:
    if len(chunks) != len(vectors):
        raise ValueError("chunks and vectors length mismatch")
    if not chunks:
        return 0

    settings = settings or get_settings()
    ensure_email_messages_collection(url, vector_size=len(vectors[0]))
    delete_email_points(url=url, email_id=email_id)

    points = [
        PointStruct(
            id=_point_id(email_id, chunk["chunk_index"]),
            vector=vector,
            payload={
                "email_id": email_id,
                "message_id": chunk.get("message_id"),
                "mailbox": chunk.get("mailbox"),
                "subject": chunk.get("subject"),
                "status": chunk.get("status"),
                "department_id": chunk.get("department_id"),
                "received_at": chunk.get("received_at"),
                "chunk_index": chunk["chunk_index"],
                "chunk_count": len(chunks),
                "chunk_text": chunk.get("chunk_text", "")[:2000],
                "attachment_filenames": list(chunk.get("attachment_filenames") or []),
                "has_attachments": bool(chunk.get("has_attachments")),
            },
        )
        for chunk, vector in zip(chunks, vectors, strict=True)
    ]

    client = QdrantClient(url=url, prefer_grpc=False)
    try:
        client.upsert(collection_name=EMAIL_MESSAGES_COLLECTION, points=points)
    finally:
        client.close()
    return len(points)


def search_similar_emails(
    *,
    url: str,
    query_vector: list[float],
    limit: int = 10,
    mailbox: str | None = None,
) -> list[dict[str, Any]]:
    client = QdrantClient(url=url, prefer_grpc=False)
    try:
        query_filter = None
        if mailbox:
            query_filter = Filter(
                must=[FieldCondition(key="mailbox", match=MatchValue(value=mailbox))]
            )
        hits = client.query_points(
            collection_name=EMAIL_MESSAGES_COLLECTION,
            query=query_vector,
            query_filter=query_filter,
            limit=limit,
            with_payload=True,
        ).points
        return [
            {
                "score": hit.score,
                "email_id": (hit.payload or {}).get("email_id"),
                "message_id": (hit.payload or {}).get("message_id"),
                "subject": (hit.payload or {}).get("subject"),
                "chunk_index": (hit.payload or {}).get("chunk_index"),
            }
            for hit in hits
        ]
    finally:
        client.close()


def ensure_department_corrections_collection(
    url: str,
    *,
    vector_size: int | None = None,
) -> None:
    settings = get_settings()
    size = vector_size or settings.embedding_vector_size
    client = QdrantClient(url=url, prefer_grpc=False)
    try:
        existing = {c.name for c in client.get_collections().collections}
        if DEPARTMENT_CORRECTIONS_COLLECTION not in existing:
            client.create_collection(
                DEPARTMENT_CORRECTIONS_COLLECTION,
                vectors_config=VectorParams(size=size, distance=Distance.COSINE),
            )
        for field in (
            "record_id",
            "email_id",
            "recipient",
            "dept_correct_id",
            "dept_wrong_id",
            "source",
        ):
            try:
                client.create_payload_index(
                    collection_name=DEPARTMENT_CORRECTIONS_COLLECTION,
                    field_name=field,
                    field_schema=PayloadSchemaType.KEYWORD,
                )
            except Exception:
                pass
    finally:
        client.close()


def delete_department_correction_points(*, url: str, record_id: str) -> int:
    client = QdrantClient(url=url, prefer_grpc=False)
    deleted = 0
    try:
        while True:
            points, next_offset = client.scroll(
                collection_name=DEPARTMENT_CORRECTIONS_COLLECTION,
                scroll_filter=Filter(
                    must=[FieldCondition(key="record_id", match=MatchValue(value=record_id))]
                ),
                limit=256,
                with_payload=False,
                with_vectors=False,
            )
            if not points:
                break
            ids = [point.id for point in points]
            client.delete(collection_name=DEPARTMENT_CORRECTIONS_COLLECTION, points_selector=ids)
            deleted += len(ids)
            if next_offset is None:
                break
    finally:
        client.close()
    return deleted


def purge_department_corrections_collection(
    *,
    url: str,
    before_iso: str | None = None,
    delete_all: bool = False,
) -> dict[str, int]:
    """Удаляет точки department_corrections_bge (все или corrected_at < before_iso)."""
    client = QdrantClient(url=url, prefer_grpc=False)
    deleted = 0
    scanned = 0
    try:
        offset = None
        while True:
            points, offset = client.scroll(
                collection_name=DEPARTMENT_CORRECTIONS_COLLECTION,
                limit=256,
                offset=offset,
                with_payload=True,
                with_vectors=False,
            )
            if not points:
                break
            to_delete: list = []
            for point in points:
                scanned += 1
                if delete_all:
                    to_delete.append(point.id)
                    continue
                if not before_iso:
                    continue
                corrected = str((point.payload or {}).get("corrected_at") or "")
                if corrected and corrected < before_iso:
                    to_delete.append(point.id)
            if to_delete:
                client.delete(
                    collection_name=DEPARTMENT_CORRECTIONS_COLLECTION,
                    points_selector=to_delete,
                )
                deleted += len(to_delete)
            if offset is None:
                break
    finally:
        client.close()
    return {"scanned": scanned, "deleted": deleted}


def upsert_department_correction(
    *,
    url: str,
    record_id: str,
    embed_text: str,
    vector: list[float],
    payload: dict[str, Any],
    settings: Settings | None = None,
) -> None:
    settings = settings or get_settings()
    ensure_department_corrections_collection(url, vector_size=len(vector))
    delete_department_correction_points(url=url, record_id=record_id)
    point = PointStruct(
        id=_correction_point_id(record_id, 0),
        vector=vector,
        payload={
            **payload,
            "record_id": record_id,
            "embedding_text": embed_text[:2000],
        },
    )
    client = QdrantClient(url=url, prefer_grpc=False)
    try:
        client.upsert(collection_name=DEPARTMENT_CORRECTIONS_COLLECTION, points=[point])
    finally:
        client.close()


def search_department_corrections(
    *,
    url: str,
    query_vector: list[float],
    limit: int = 5,
    recipient: str | None = None,
) -> list[dict[str, Any]]:
    from qdrant_client.models import FieldCondition, Filter, MatchValue

    client = QdrantClient(url=url, prefer_grpc=False)
    try:
        query_filter = None
        if recipient:
            query_filter = Filter(
                must=[FieldCondition(key="recipient", match=MatchValue(value=recipient))]
            )
        hits = client.query_points(
            collection_name=DEPARTMENT_CORRECTIONS_COLLECTION,
            query=query_vector,
            query_filter=query_filter,
            limit=limit,
            with_payload=True,
        ).points
        if not hits and query_filter is not None:
            hits = client.query_points(
                collection_name=DEPARTMENT_CORRECTIONS_COLLECTION,
                query=query_vector,
                limit=limit,
                with_payload=True,
            ).points
        return [
            {
                "score": hit.score,
                "dept_correct_id": (hit.payload or {}).get("dept_correct_id"),
                "dept_correct_name": (hit.payload or {}).get("dept_correct_name"),
                "dept_wrong_id": (hit.payload or {}).get("dept_wrong_id"),
                "recipient": (hit.payload or {}).get("recipient"),
                "record_id": (hit.payload or {}).get("record_id"),
            }
            for hit in hits
        ]
    finally:
        client.close()
