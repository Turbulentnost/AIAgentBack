"""Qdrant-адаптер RAG (раздел 5.3, 9 ТЗ)."""

from __future__ import annotations

import logging
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

logger = logging.getLogger(__name__)

from agent_pochta.schemas import Contractor, Department
from agent_pochta.services.rag import (
    RAGService,
    StubRAGService,
    _DEMO_CONTRACTORS,
    _DEMO_DEPARTMENTS,
    score_department_keywords,
)

CONTRACTORS_COLLECTION = "contractors"
DEPARTMENTS_COLLECTION = "departments"
DUMMY_VECTOR_SIZE = 4


class QdrantRAGService(RAGService):
    """RAG через Qdrant: точный поиск контрагентов, keyword-scoring отделов."""

    def __init__(self, url: str) -> None:
        self._client = QdrantClient(url=url, prefer_grpc=False)
        self._departments: dict[str, Department] = {}
        self._ensure_collections()
        self._load_departments_cache()

    def _ensure_collections(self) -> None:
        existing = {c.name for c in self._client.get_collections().collections}
        params = VectorParams(size=DUMMY_VECTOR_SIZE, distance=Distance.COSINE)
        if CONTRACTORS_COLLECTION not in existing:
            self._client.create_collection(CONTRACTORS_COLLECTION, vectors_config=params)
        if DEPARTMENTS_COLLECTION not in existing:
            self._client.create_collection(DEPARTMENTS_COLLECTION, vectors_config=params)
        _ensure_payload_indexes(
            self._client,
            CONTRACTORS_COLLECTION,
            ("email", "contractor_id"),
        )
        _ensure_payload_indexes(
            self._client,
            DEPARTMENTS_COLLECTION,
            ("department_id",),
        )

    def _load_departments_cache(self) -> None:
        points, _ = self._client.scroll(
            collection_name=DEPARTMENTS_COLLECTION,
            limit=500,
            with_payload=True,
        )
        for point in points:
            payload = point.payload or {}
            dept = Department(
                department_id=str(payload["department_id"]),
                department_name=str(payload["department_name"]),
                head_name=str(payload.get("head_name", "")),
                responsibility=str(payload.get("responsibility", "")),
                keywords=list(payload.get("keywords") or []),
            )
            self._departments[dept.department_id] = dept

    def find_contractor_by_email(self, email: str) -> Contractor | None:
        email = email.lower().strip()
        points, _ = self._client.scroll(
            collection_name=CONTRACTORS_COLLECTION,
            scroll_filter=Filter(
                must=[FieldCondition(key="email", match=MatchValue(value=email))]
            ),
            limit=1,
            with_payload=True,
        )
        if not points:
            return None
        payload = points[0].payload or {}
        return Contractor(
            contractor_id=str(payload["contractor_id"]),
            name=str(payload["name"]),
            emails=list(payload.get("emails") or [email]),
            department_codes=list(payload.get("department_codes") or []),
            contractor_type=str(payload.get("contractor_type", "")),
        )

    def search_departments(
        self,
        text: str,
        top_k: int = 3,
        *,
        recipient: str | None = None,
    ) -> list[Department]:
        if not self._departments:
            self._load_departments_cache()
        scored: list[tuple[int, Department]] = []
        for department in self._departments.values():
            score = score_department_keywords(department, text, recipient=recipient)
            scored.append((score, department))
        scored.sort(key=lambda item: item[0], reverse=True)
        ranked = [dept for score, dept in scored if score > 0] or [dept for _, dept in scored]
        return ranked[:top_k]

    def get_department(self, department_id: str) -> Department | None:
        return self._departments.get(department_id)

    def refresh_departments_cache(self) -> None:
        """Перечитать отделы из Qdrant (после sync_rag_from_1c)."""
        self._load_departments_cache()

    def append_department_keywords(
        self,
        department_id: str,
        new_keywords: list[str],
        *,
        max_keywords: int = 200,
    ) -> dict:
        """Добавляет уникальные keywords к отделу и обновляет локальный кэш."""
        result = _append_department_keywords_impl(
            self._client,
            department_id,
            new_keywords,
            max_keywords=max_keywords,
        )
        if result["updated"]:
            self._load_departments_cache()
        return result

    def close(self) -> None:
        self._client.close()


def _append_department_keywords_impl(
    client: QdrantClient,
    department_id: str,
    new_keywords: list[str],
    *,
    max_keywords: int = 200,
) -> dict:
    points, _ = client.scroll(
        collection_name=DEPARTMENTS_COLLECTION,
        scroll_filter=Filter(
            must=[FieldCondition(key="department_id", match=MatchValue(value=department_id))]
        ),
        limit=1,
        with_payload=True,
        with_vectors=True,
    )
    if not points:
        return {
            "updated": False,
            "keywords_added": 0,
            "added_keywords": [],
            "reason": "department_not_found",
        }

    point = points[0]
    payload = dict(point.payload or {})
    existing = list(payload.get("keywords") or [])
    existing_lower = {str(k).lower() for k in existing}
    added: list[str] = []

    for raw in new_keywords:
        kw = str(raw).strip().lower()
        if len(kw) < 3 or kw in existing_lower:
            continue
        added.append(kw)
        existing_lower.add(kw)

    if not added:
        return {
            "updated": False,
            "keywords_added": 0,
            "added_keywords": [],
            "reason": "no_new_keywords",
        }

    merged = existing + added
    if len(merged) > max_keywords:
        merged = merged[-max_keywords:]

    vector = list(point.vector) if point.vector is not None else [0.0] * DUMMY_VECTOR_SIZE
    payload["keywords"] = merged
    client.upsert(
        collection_name=DEPARTMENTS_COLLECTION,
        points=[
            PointStruct(
                id=point.id,
                vector=vector,
                payload=payload,
            )
        ],
    )
    return {
        "updated": True,
        "keywords_added": len(added),
        "added_keywords": added,
    }


def append_department_keywords(
    url: str,
    department_id: str,
    new_keywords: list[str],
    *,
    max_keywords: int = 200,
) -> dict:
    """Добавляет keywords к отделу в Qdrant (дообучение RAG fallback)."""
    client = QdrantClient(url=url, prefer_grpc=False)
    try:
        return _append_department_keywords_impl(
            client,
            department_id,
            new_keywords,
            max_keywords=max_keywords,
        )
    finally:
        client.close()


def upsert_rag_catalog(
    url: str,
    contractors: list[Contractor],
    departments: list[Department],
    *,
    replace: bool = True,
) -> tuple[int, int]:
    """Полная синхронизация коллекций contractors / departments в Qdrant."""
    client = QdrantClient(url=url, prefer_grpc=False)
    if replace:
        existing = {c.name for c in client.get_collections().collections}
        for name in (CONTRACTORS_COLLECTION, DEPARTMENTS_COLLECTION):
            if name in existing:
                client.delete_collection(name)

    service = QdrantRAGService(url)
    client = service._client

    contractor_points: list[PointStruct] = []
    for contractor in contractors:
        for email in contractor.emails:
            contractor_points.append(
                PointStruct(
                    id=str(uuid.uuid4()),
                    vector=[0.0] * DUMMY_VECTOR_SIZE,
                    payload={
                        "email": email.lower().strip(),
                        "contractor_id": contractor.contractor_id,
                        "name": contractor.name,
                        "emails": contractor.emails,
                        "department_codes": contractor.department_codes,
                        "contractor_type": contractor.contractor_type,
                    },
                )
            )

    department_points = [
        PointStruct(
            id=str(uuid.uuid4()),
            vector=[0.0] * DUMMY_VECTOR_SIZE,
            payload={
                "department_id": department.department_id,
                "department_name": department.department_name,
                "head_name": department.head_name,
                "responsibility": department.responsibility,
                "keywords": department.keywords,
            },
        )
        for department in departments
    ]

    if contractor_points:
        client.upsert(collection_name=CONTRACTORS_COLLECTION, points=contractor_points)
    if department_points:
        client.upsert(collection_name=DEPARTMENTS_COLLECTION, points=department_points)

    service._load_departments_cache()
    return len(contractor_points), len(department_points)


def upsert_departments_only(
    url: str,
    departments: list[Department],
    *,
    replace: bool = True,
) -> int:
    """Синхронизация только коллекции departments; contractors не затрагиваются."""
    client = QdrantClient(url=url, prefer_grpc=False)
    existing = {c.name for c in client.get_collections().collections}
    params = VectorParams(size=DUMMY_VECTOR_SIZE, distance=Distance.COSINE)

    if DEPARTMENTS_COLLECTION in existing and replace:
        client.delete_collection(DEPARTMENTS_COLLECTION)
    if DEPARTMENTS_COLLECTION not in {c.name for c in client.get_collections().collections}:
        client.create_collection(DEPARTMENTS_COLLECTION, vectors_config=params)

    department_points = [
        PointStruct(
            id=str(uuid.uuid4()),
            vector=[0.0] * DUMMY_VECTOR_SIZE,
            payload={
                "department_id": department.department_id,
                "department_name": department.department_name,
                "head_name": department.head_name,
                "responsibility": department.responsibility,
                "keywords": department.keywords,
            },
        )
        for department in departments
    ]

    if department_points:
        client.upsert(collection_name=DEPARTMENTS_COLLECTION, points=department_points)

    client.close()
    return len(department_points)


def seed_qdrant(url: str) -> tuple[int, int]:
    """Загружает демо-данные в Qdrant. Возвращает (contractors, departments)."""
    return upsert_rag_catalog(url, _DEMO_CONTRACTORS, _DEMO_DEPARTMENTS, replace=True)


def upsert_contractors_merge(url: str, contractors: list[Contractor]) -> int:
    """Добавляет/обновляет контрагентов в Qdrant без затрагивания departments."""
    if not contractors:
        return 0

    client = QdrantClient(url=url, prefer_grpc=False)
    try:
        existing = {c.name for c in client.get_collections().collections}
        params = VectorParams(size=DUMMY_VECTOR_SIZE, distance=Distance.COSINE)
        if CONTRACTORS_COLLECTION not in existing:
            client.create_collection(CONTRACTORS_COLLECTION, vectors_config=params)
        _ensure_payload_indexes(
            client,
            CONTRACTORS_COLLECTION,
            ("email", "contractor_id"),
        )

        upserted = 0
        for contractor in contractors:
            for email in contractor.emails:
                normalized = email.lower().strip()
                if not normalized:
                    continue
                points, _ = client.scroll(
                    collection_name=CONTRACTORS_COLLECTION,
                    scroll_filter=Filter(
                        must=[FieldCondition(key="email", match=MatchValue(value=normalized))]
                    ),
                    limit=1,
                    with_payload=True,
                    with_vectors=True,
                )
                payload = {
                    "email": normalized,
                    "contractor_id": contractor.contractor_id,
                    "name": contractor.name,
                    "emails": contractor.emails,
                    "department_codes": contractor.department_codes,
                    "contractor_type": contractor.contractor_type,
                }
                if points:
                    point = points[0]
                    vector = list(point.vector) if point.vector is not None else [0.0] * DUMMY_VECTOR_SIZE
                    client.upsert(
                        collection_name=CONTRACTORS_COLLECTION,
                        points=[PointStruct(id=point.id, vector=vector, payload=payload)],
                    )
                else:
                    client.upsert(
                        collection_name=CONTRACTORS_COLLECTION,
                        points=[
                            PointStruct(
                                id=str(uuid.uuid4()),
                                vector=[0.0] * DUMMY_VECTOR_SIZE,
                                payload=payload,
                            )
                        ],
                    )
                upserted += 1
        return upserted
    finally:
        client.close()


def search_contractors(
    url: str,
    query: str,
    *,
    limit: int = 20,
) -> list[dict]:
    """Поиск контрагентов по префиксу/подстроке имени или email."""
    q = query.strip().lower()
    if len(q) < 2:
        return []

    client = QdrantClient(url=url, prefer_grpc=False)
    try:
        existing = {c.name for c in client.get_collections().collections}
        if CONTRACTORS_COLLECTION not in existing:
            return []

        seen: set[str] = set()
        results: list[dict] = []
        offset = None
        while True:
            points, offset = client.scroll(
                collection_name=CONTRACTORS_COLLECTION,
                limit=256,
                offset=offset,
                with_payload=True,
            )
            if not points:
                break
            for point in points:
                payload = point.payload or {}
                contractor_id = str(payload.get("contractor_id") or "")
                name = str(payload.get("name") or "")
                email = str(payload.get("email") or "")
                key = contractor_id or email
                if not key or key in seen:
                    continue
                haystack = f"{name} {email}".lower()
                if q not in haystack:
                    continue
                seen.add(key)
                results.append(
                    {
                        "contractor_id": contractor_id,
                        "name": name,
                        "email": email,
                        "emails": list(payload.get("emails") or ([email] if email else [])),
                        "contractor_type": str(payload.get("contractor_type") or ""),
                    }
                )
            if offset is None:
                break

        results.sort(key=lambda item: (item["name"].lower(), item["email"]))
        return results[:limit]
    finally:
        client.close()


def _ensure_payload_indexes(
    client: QdrantClient,
    collection: str,
    fields: tuple[str, ...],
) -> None:
    for field in fields:
        try:
            client.create_payload_index(
                collection_name=collection,
                field_name=field,
                field_schema=PayloadSchemaType.KEYWORD,
            )
        except Exception:
            pass


def build_rag_service(settings) -> RAGService:
    """Stub или Qdrant в зависимости от RAG_BACKEND."""
    if settings.rag_backend != "qdrant":
        return StubRAGService()
    try:
        return QdrantRAGService(settings.qdrant_url)
    except Exception as exc:
        logger.warning(
            "Qdrant unavailable at %s (%s), falling back to StubRAGService",
            settings.qdrant_url,
            exc,
        )
        return StubRAGService()
