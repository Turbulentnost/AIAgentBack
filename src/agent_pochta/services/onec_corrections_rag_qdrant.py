"""Qdrant-коллекция onec_corrections для HITL-коррекций партнёра / организации."""

from __future__ import annotations

import uuid

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    PayloadSchemaType,
    PointStruct,
    VectorParams,
)

ONEC_CORRECTIONS_COLLECTION = "onec_corrections"
DUMMY_VECTOR_SIZE = 4


def _entry_payload(entry: dict) -> dict:
    return {
        "partner": entry.get("partner") or None,
        "organization": entry.get("organization") or None,
        "department_id": entry.get("department_id"),
        "department_name": entry.get("department_name"),
        "sender_email": (entry.get("sender_email") or "").lower().strip() or None,
        "recipient": entry.get("recipient"),
        "subject": entry.get("subject") or "",
        "keywords": list(entry.get("keywords") or []),
        "created_at": entry.get("created_at"),
    }


def _payload_to_entry(point_id: str, payload: dict) -> dict:
    return {
        "id": str(point_id),
        "partner": payload.get("partner"),
        "organization": payload.get("organization"),
        "department_id": payload.get("department_id"),
        "department_name": payload.get("department_name"),
        "sender_email": (payload.get("sender_email") or "").lower().strip(),
        "recipient": payload.get("recipient"),
        "subject": payload.get("subject") or "",
        "keywords": list(payload.get("keywords") or []),
        "created_at": payload.get("created_at"),
    }


def _ensure_collection(client: QdrantClient) -> None:
    existing = {c.name for c in client.get_collections().collections}
    if ONEC_CORRECTIONS_COLLECTION not in existing:
        client.create_collection(
            ONEC_CORRECTIONS_COLLECTION,
            vectors_config=VectorParams(size=DUMMY_VECTOR_SIZE, distance=Distance.COSINE),
        )
    _ensure_payload_indexes(client)


def _ensure_payload_indexes(client: QdrantClient) -> None:
    for field in ("sender_email", "recipient", "organization", "department_id"):
        try:
            client.create_payload_index(
                collection_name=ONEC_CORRECTIONS_COLLECTION,
                field_name=field,
                field_schema=PayloadSchemaType.KEYWORD,
            )
        except Exception:
            pass


def ensure_onec_corrections_indexes(url: str) -> None:
    """Создаёт коллекцию onec_corrections и keyword-индексы (init / seed)."""
    client = QdrantClient(url=url, prefer_grpc=False)
    try:
        _ensure_collection(client)
    finally:
        client.close()


def upsert_onec_correction_entry(url: str, entry: dict) -> None:
    client = QdrantClient(url=url, prefer_grpc=False)
    try:
        _ensure_collection(client)
        client.upsert(
            collection_name=ONEC_CORRECTIONS_COLLECTION,
            points=[
                PointStruct(
                    id=entry.get("id") or str(uuid.uuid4()),
                    vector=[0.0] * DUMMY_VECTOR_SIZE,
                    payload=_entry_payload(entry),
                )
            ],
        )
    finally:
        client.close()


def list_onec_corrections_in_qdrant(url: str) -> list[dict]:
    client = QdrantClient(url=url, prefer_grpc=False)
    try:
        _ensure_collection(client)
        existing = {c.name for c in client.get_collections().collections}
        if ONEC_CORRECTIONS_COLLECTION not in existing:
            return []
        points, _ = client.scroll(
            collection_name=ONEC_CORRECTIONS_COLLECTION,
            limit=2000,
            with_payload=True,
        )
        return [_payload_to_entry(str(point.id), point.payload or {}) for point in points]
    finally:
        client.close()


def prune_onec_corrections_orphans(url: str, valid_entry_ids: set[str]) -> int:
    """Delete Qdrant points whose id is not in the current JSON store."""
    if not valid_entry_ids:
        return 0
    client = QdrantClient(url=url, prefer_grpc=False)
    try:
        _ensure_collection(client)
        points, _ = client.scroll(
            collection_name=ONEC_CORRECTIONS_COLLECTION,
            limit=2000,
            with_payload=False,
        )
        stale = [point.id for point in points if str(point.id) not in valid_entry_ids]
        if not stale:
            return 0
        client.delete(collection_name=ONEC_CORRECTIONS_COLLECTION, points_selector=stale)
        return len(stale)
    finally:
        client.close()
