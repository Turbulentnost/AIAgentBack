from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from qdrant_client import AsyncQdrantClient
from qdrant_client.http import models as qmodels

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class QdrantClientError(RuntimeError):
    """Raised when Qdrant integration cannot complete an operation."""


@dataclass(frozen=True)
class QdrantPoint:
    id: str
    vector: list[float]
    payload: dict[str, Any]


@dataclass(frozen=True)
class QdrantSearchResult:
    id: str
    score: float
    payload: dict[str, Any]


class QdrantVectorClient:
    """Single backend entrypoint for Qdrant collection, indexing and search operations."""

    def __init__(self, collection: str | None = None) -> None:
        self.collection = collection or settings.QDRANT_COLLECTION
        self._client: AsyncQdrantClient | None = None

    @property
    def client(self) -> AsyncQdrantClient:
        if self._client is None:
            self._client = AsyncQdrantClient(
                url=settings.QDRANT_URL,
                api_key=settings.QDRANT_API_KEY,
            )
        return self._client

    def reset_client(self) -> None:
        """Сбрасывает кэшированный async-клиент.

        Каждая задача Celery выполняется в новом event loop, а
        AsyncQdrantClient привязан к loop, в котором был создан. Без сброса
        повторная задача падает с «Event loop is closed»."""
        self._client = None

    async def aclose(self) -> None:
        if self._client is not None:
            try:
                await self._client.close()
            except Exception:
                pass
            self._client = None

    async def health_check(self) -> bool:
        try:
            await self.client.get_collections()
            return True
        except Exception as exc:
            logger.error("qdrant.health.failed", error=str(exc))
            return False

    async def ensure_collection(
        self,
        *,
        collection: str | None = None,
        vector_size: int | None = None,
        distance: qmodels.Distance = qmodels.Distance.COSINE,
    ) -> None:
        collection_name = collection or self.collection
        size = vector_size or settings.QDRANT_VECTOR_SIZE
        try:
            if await self.client.collection_exists(collection_name):
                await self._validate_collection_size(collection_name, size)
                return

            await self.client.create_collection(
                collection_name=collection_name,
                vectors_config=qmodels.VectorParams(size=size, distance=distance),
            )
            logger.info("qdrant.collection.created", collection=collection_name, vector_size=size)
        except Exception as exc:
            logger.error(
                "qdrant.collection.ensure_failed",
                collection=collection_name,
                vector_size=size,
                error=str(exc),
            )
            raise QdrantClientError(f"Не удалось подготовить коллекцию Qdrant: {exc}") from exc

    async def upsert_points(
        self,
        points: list[QdrantPoint],
        *,
        collection: str | None = None,
        ensure_collection: bool = True,
        vector_size: int | None = None,
    ) -> int:
        if not points:
            return 0

        collection_name = collection or self.collection
        expected_size = vector_size or settings.QDRANT_VECTOR_SIZE
        self._validate_vectors(points, expected_size)

        try:
            if ensure_collection:
                await self.ensure_collection(collection=collection_name, vector_size=expected_size)

            await self.client.upsert(
                collection_name=collection_name,
                points=[
                    qmodels.PointStruct(
                        id=point.id,
                        vector=point.vector,
                        payload=point.payload,
                    )
                    for point in points
                ],
                wait=True,
            )
            logger.info("qdrant.points.upserted", collection=collection_name, count=len(points))
            return len(points)
        except QdrantClientError:
            raise
        except Exception as exc:
            logger.error(
                "qdrant.points.upsert_failed",
                collection=collection_name,
                count=len(points),
                error=str(exc),
            )
            raise QdrantClientError(f"Не удалось сохранить embeddings в Qdrant: {exc}") from exc

    async def search(
        self,
        query_vector: list[float],
        *,
        collection: str | None = None,
        top_k: int = 5,
        filters: dict[str, Any] | None = None,
        score_threshold: float | None = None,
    ) -> list[QdrantSearchResult]:
        collection_name = collection or self.collection
        if not query_vector:
            raise QdrantClientError("Пустой embedding для поиска в Qdrant")

        try:
            result = await self.client.query_points(
                collection_name=collection_name,
                query=query_vector,
                query_filter=self._build_filter(filters),
                limit=top_k,
                score_threshold=score_threshold,
                with_payload=True,
            )
            points = getattr(result, "points", result)
            return [
                QdrantSearchResult(
                    id=str(item.id),
                    score=float(item.score),
                    payload=dict(item.payload or {}),
                )
                for item in points
            ]
        except Exception as exc:
            logger.error(
                "qdrant.search.failed",
                collection=collection_name,
                filters=filters,
                error=str(exc),
            )
            raise QdrantClientError(f"Не удалось выполнить поиск в Qdrant: {exc}") from exc

    async def delete_by_document_version(
        self,
        document_version_id: str,
        *,
        collection: str | None = None,
    ) -> None:
        collection_name = collection or self.collection
        try:
            if not await self.client.collection_exists(collection_name):
                logger.info(
                    "qdrant.points.delete_skipped_missing_collection",
                    collection=collection_name,
                    document_version_id=document_version_id,
                )
                return

            await self.client.delete(
                collection_name=collection_name,
                points_selector=qmodels.FilterSelector(
                    filter=self._build_filter({"document_version_id": document_version_id})
                ),
                wait=True,
            )
            logger.info(
                "qdrant.points.deleted_by_document_version",
                collection=collection_name,
                document_version_id=document_version_id,
            )
        except Exception as exc:
            logger.error(
                "qdrant.points.delete_failed",
                collection=collection_name,
                document_version_id=document_version_id,
                error=str(exc),
            )
            raise QdrantClientError(
                f"Не удалось удалить embeddings версии документа из Qdrant: {exc}"
            ) from exc

    async def delete_by_filter(
        self,
        filters: dict[str, Any],
        *,
        collection: str | None = None,
    ) -> None:
        collection_name = collection or self.collection
        try:
            if not await self.client.collection_exists(collection_name):
                logger.info(
                    "qdrant.points.delete_skipped_missing_collection",
                    collection=collection_name,
                    filters=filters,
                )
                return

            await self.client.delete(
                collection_name=collection_name,
                points_selector=qmodels.FilterSelector(filter=self._build_filter(filters)),
                wait=True,
            )
            logger.info("qdrant.points.deleted_by_filter", collection=collection_name, filters=filters)
        except Exception as exc:
            logger.error(
                "qdrant.points.delete_by_filter_failed",
                collection=collection_name,
                filters=filters,
                error=str(exc),
            )
            raise QdrantClientError(f"Не удалось удалить embeddings из Qdrant: {exc}") from exc

    def _build_filter(self, filters: dict[str, Any] | None) -> qmodels.Filter | None:
        if not filters:
            return None

        conditions: list[qmodels.FieldCondition] = []
        for key, value in filters.items():
            if value is None:
                continue
            if isinstance(value, (list, tuple, set)):
                values = [item for item in value if item is not None]
                if not values:
                    continue
                conditions.append(
                    qmodels.FieldCondition(
                        key=key,
                        match=qmodels.MatchAny(any=list(values)),
                    )
                )
                continue
            conditions.append(
                qmodels.FieldCondition(
                    key=key,
                    match=qmodels.MatchValue(value=value),
                )
            )

        return qmodels.Filter(must=conditions) if conditions else None

    def _validate_vectors(self, points: list[QdrantPoint], expected_size: int) -> None:
        invalid = [point.id for point in points if len(point.vector) != expected_size]
        if invalid:
            raise QdrantClientError(
                "Размерность embedding не совпадает с настройкой "
                f"QDRANT_VECTOR_SIZE={expected_size}. Некорректные точки: {', '.join(invalid[:5])}"
            )

    async def _validate_collection_size(self, collection_name: str, expected_size: int) -> None:
        collection = await self.client.get_collection(collection_name)
        vectors = getattr(getattr(getattr(collection, "config", None), "params", None), "vectors", None)
        actual_size = getattr(vectors, "size", None)
        if actual_size is not None and actual_size != expected_size:
            raise QdrantClientError(
                f"Коллекция Qdrant {collection_name} имеет размерность {actual_size}, "
                f"а ожидается {expected_size}. Пересоздайте коллекцию перед индексацией BGE-M3."
            )


qdrant_client = QdrantVectorClient()
