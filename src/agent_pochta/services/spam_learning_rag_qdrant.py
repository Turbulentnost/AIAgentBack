"""Qdrant-коллекция spam_learning для обучения спам-фильтра (spam / not_spam)."""

from __future__ import annotations

import uuid

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

from agent_pochta.routing.normalize import normalize_text
from agent_pochta.rules.spam_learning import (
    SPAM_LEARNING_COLLECTION,
    _normalize_entry,
    reason_indicates_not_spam,
)

DUMMY_VECTOR_SIZE = 4


def _entry_payload(entry: dict) -> dict:
    normalized = _normalize_entry(entry)
    return {
        "message_id": normalized.get("message_id"),
        "sender_email": normalized.get("sender_email"),
        "keywords": list(normalized.get("keywords") or []),
        "reason": normalized.get("reason"),
        "label": normalized.get("label"),
        "created_at": normalized.get("created_at"),
    }


def _payload_to_entry(point_id: str, payload: dict) -> dict:
    return _normalize_entry(
        {
            "id": str(point_id),
            "message_id": payload.get("message_id"),
            "sender_email": payload.get("sender_email"),
            "keywords": list(payload.get("keywords") or []),
            "reason": payload.get("reason") or payload.get("spam_reason"),
            "label": payload.get("label"),
            "created_at": payload.get("created_at"),
        }
    )


def _ensure_collection(client: QdrantClient) -> None:
    existing = {c.name for c in client.get_collections().collections}
    if SPAM_LEARNING_COLLECTION not in existing:
        client.create_collection(
            SPAM_LEARNING_COLLECTION,
            vectors_config=VectorParams(size=DUMMY_VECTOR_SIZE, distance=Distance.COSINE),
        )
    _ensure_payload_indexes(client)


def _ensure_payload_indexes(client: QdrantClient) -> None:
    for field in ("message_id", "label", "sender_email"):
        try:
            client.create_payload_index(
                collection_name=SPAM_LEARNING_COLLECTION,
                field_name=field,
                field_schema=PayloadSchemaType.KEYWORD,
            )
        except Exception:
            pass


def ensure_spam_learning_indexes(url: str) -> None:
    """Создаёт коллекцию spam_learning и keyword-индексы (init / seed)."""
    client = QdrantClient(url=url, prefer_grpc=False)
    try:
        _ensure_collection(client)
    finally:
        client.close()


def upsert_spam_learning_entry(url: str, entry: dict) -> None:
    client = QdrantClient(url=url, prefer_grpc=False)
    try:
        _ensure_collection(client)
        client.upsert(
            collection_name=SPAM_LEARNING_COLLECTION,
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


def delete_spam_learning_by_message_id(
    url: str,
    message_id: str,
    *,
    label: str | None = None,
) -> int:
    client = QdrantClient(url=url, prefer_grpc=False)
    try:
        _ensure_collection(client)
        must = [FieldCondition(key="message_id", match=MatchValue(value=message_id))]
        if label:
            must.append(FieldCondition(key="label", match=MatchValue(value=label)))
        points, _ = client.scroll(
            collection_name=SPAM_LEARNING_COLLECTION,
            scroll_filter=Filter(must=must),
            limit=100,
            with_payload=False,
        )
        if not points:
            return 0
        client.delete(
            collection_name=SPAM_LEARNING_COLLECTION,
            points_selector=[point.id for point in points],
        )
        return len(points)
    finally:
        client.close()


def list_spam_learning_in_qdrant(url: str) -> list[dict]:
    client = QdrantClient(url=url, prefer_grpc=False)
    try:
        _ensure_collection(client)
        existing = {c.name for c in client.get_collections().collections}
        if SPAM_LEARNING_COLLECTION not in existing:
            return []
        points, _ = client.scroll(
            collection_name=SPAM_LEARNING_COLLECTION,
            limit=1000,
            with_payload=True,
        )
        return [_payload_to_entry(str(point.id), point.payload or {}) for point in points]
    finally:
        client.close()




def prune_spam_learning_orphans(url: str, valid_entry_ids: set[str]) -> int:
    """Delete Qdrant points whose id is not in the current JSON store."""
    if not valid_entry_ids:
        return 0
    client = QdrantClient(url=url, prefer_grpc=False)
    try:
        _ensure_collection(client)
        points, _ = client.scroll(
            collection_name=SPAM_LEARNING_COLLECTION,
            limit=2000,
            with_payload=False,
        )
        stale = [point.id for point in points if str(point.id) not in valid_entry_ids]
        if not stale:
            return 0
        client.delete(collection_name=SPAM_LEARNING_COLLECTION, points_selector=stale)
        return len(stale)
    finally:
        client.close()


def find_spam_learning_match(
    url: str,
    *,
    sender_email: str,
    subject: str,
    body: str,
    label: str | None = None,
) -> dict | None:
    entries = list_spam_learning_in_qdrant(url)
    if label:
        entries = [e for e in entries if e.get("label") == label]
    entries.sort(key=lambda item: item.get("created_at") or "", reverse=True)

    sender_email = sender_email.lower().strip()
    text = normalize_text(f"{subject} {body}")

    for entry in entries:
        entry_sender = (entry.get("sender_email") or "").lower().strip()
        if entry_sender and entry_sender != sender_email:
            continue
        keywords = entry.get("keywords") or []
        keyword_hits = sum(1 for kw in keywords if kw and kw in text)
        if keywords and keyword_hits == 0 and not entry_sender:
            continue
        score = 0
        if entry_sender:
            score += 3
        score += keyword_hits
        if score > 0:
            label = entry.get("label")
            reason = str(entry.get("reason") or "")
            if label == "spam" and reason_indicates_not_spam(reason):
                continue
            return entry
    return None
