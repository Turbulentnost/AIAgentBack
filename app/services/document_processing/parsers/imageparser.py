from __future__ import annotations

import base64
import json
import time
import uuid
from dataclasses import dataclass
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.documents.storage import object_storage
from app.models.document import Document, DocumentVersion
from app.models.enums import DocumentProcessingStatus, TextExtractStatus
from app.services.document_processing.chunking import DocumentChunkingService, ParsedBlock


class ImageParsingError(RuntimeError):
    pass


@dataclass
class ImageParseResult:
    document_id: uuid.UUID
    document_version_id: uuid.UUID
    text: str
    extracted_text_object_name: str
    extraction_method: str
    characters_count: int
    duration_ms: int
    quality_notes: str | None = None


class ImageParsingService:
    """OCR image documents stored in MinIO through LM Studio vision models."""

    SUPPORTED_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp"}

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def parse_document(
        self,
        *,
        document_id: uuid.UUID | None = None,
        document_version_id: uuid.UUID | None = None,
    ) -> ImageParseResult:
        started = time.perf_counter()
        document, document_version = await self._load_document(
            document_id=document_id,
            document_version_id=document_version_id,
        )

        content_type = document.content_type or document_version.content_type
        if not document.object_name:
            raise ImageParsingError("У документа нет object_name MinIO")
        if content_type not in self.SUPPORTED_CONTENT_TYPES:
            raise ImageParsingError(f"Документ не является поддерживаемым изображением: {content_type}")

        try:
            image_data = object_storage.get_object(document.object_name)
            response = await self._extract_with_vision(image_data, content_type)
            text, quality_notes = self._parse_vision_response(response)
            extracted_text_object_name = self._save_extraction_result(
                document=document,
                document_version=document_version,
                text=text,
                quality_notes=quality_notes,
                content_type=content_type,
            )
            result = ImageParseResult(
                document_id=document.id,
                document_version_id=document_version.id,
                text=text,
                extracted_text_object_name=extracted_text_object_name,
                extraction_method="vision_ocr",
                characters_count=len(text),
                duration_ms=int((time.perf_counter() - started) * 1000),
                quality_notes=quality_notes,
            )
            await self._persist_result(document, document_version, result)
            return result
        except Exception as exc:
            await self._mark_failed(document, document_version, exc, started)
            if isinstance(exc, ImageParsingError):
                raise
            raise ImageParsingError("Не удалось обработать изображение через OCR") from exc

    async def _load_document(
        self,
        *,
        document_id: uuid.UUID | None,
        document_version_id: uuid.UUID | None,
    ) -> tuple[Document, DocumentVersion]:
        if document_version_id:
            document_version = await self.db.get(DocumentVersion, document_version_id)
            if not document_version:
                raise ImageParsingError("Версия документа не найдена")
            document = await self.db.get(Document, document_version.document_id)
            if not document:
                raise ImageParsingError("Документ не найден")
            return document, document_version

        if not document_id:
            raise ImageParsingError("Нужно передать document_id или document_version_id")

        document = await self.db.get(Document, document_id)
        if not document:
            raise ImageParsingError("Документ не найден")

        result = await self.db.execute(
            select(DocumentVersion)
            .where(DocumentVersion.document_id == document.id)
            .order_by(DocumentVersion.is_current.desc(), DocumentVersion.version_number.desc())
            .limit(1)
        )
        document_version = result.scalar_one_or_none()
        if not document_version:
            raise ImageParsingError("У документа нет версий")
        return document, document_version

    async def _extract_with_vision(self, image_data: bytes, content_type: str) -> str:
        url = f"{settings.VISION_LM_STUDIO_BASE_URL.rstrip('/')}/chat/completions"
        image_base64 = base64.b64encode(image_data).decode("ascii")
        payload = {
            "model": settings.VISION_LM_STUDIO_MODEL,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "Извлеки весь читаемый текст, таблицы, реквизиты, подписи, печати "
                                "и структуру с изображения документа. Верни строго JSON вида "
                                "{\"text\": \"...\", \"tables\": [], \"quality_notes\": \"...\"}. "
                                "Не добавляй пояснения вне JSON."
                            ),
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{content_type};base64,{image_base64}",
                            },
                        },
                    ],
                }
            ],
            "temperature": 0,
            "max_tokens": 4096,
        }
        async with httpx.AsyncClient(timeout=180) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()
            data = response.json()
        return str(data["choices"][0]["message"]["content"])

    def _parse_vision_response(self, response: str) -> tuple[str, str | None]:
        try:
            payload = json.loads(self._strip_code_fence(response))
            text = str(payload.get("text", "")).strip()
            quality_notes = payload.get("quality_notes")
            if text:
                return text, str(quality_notes) if quality_notes else None
        except Exception:
            pass
        return response.strip(), None

    def _strip_code_fence(self, value: str) -> str:
        stripped = value.strip()
        if stripped.startswith("```"):
            stripped = stripped.split("\n", 1)[-1]
            stripped = stripped.rsplit("```", 1)[0]
        return stripped.strip()

    def _save_extraction_result(
        self,
        *,
        document: Document,
        document_version: DocumentVersion,
        text: str,
        quality_notes: str | None,
        content_type: str,
    ) -> str:
        object_name = f"documents/{document.id}/extracted_text/image_v{document_version.version_number}.json"
        payload = {
            "document_id": str(document.id),
            "document_version_id": str(document_version.id),
            "content_type": content_type,
            "text": text,
            "quality_notes": quality_notes,
        }
        object_storage.put_object(
            object_name,
            json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            "application/json",
        )
        return object_name

    async def _persist_result(
        self,
        document: Document,
        document_version: DocumentVersion,
        result: ImageParseResult,
    ) -> None:
        document.pages_count = 1
        document.processing_status = DocumentProcessingStatus.TEXT_EXTRACTED
        document.text_extract_status = TextExtractStatus.EXTRACTED
        document.extracted_text_object_name = result.extracted_text_object_name
        document.metadata_ = self._merge_metadata(document.metadata_, result)

        document_version.pages_count = 1
        document_version.processing_status = DocumentProcessingStatus.TEXT_EXTRACTED
        document_version.text_extract_status = TextExtractStatus.EXTRACTED
        document_version.extracted_text_object_name = result.extracted_text_object_name
        document_version.metadata_ = self._merge_metadata(document_version.metadata_, result)

        await DocumentChunkingService(self.db).replace_chunks(
            document_id=document.id,
            document_version_id=document_version.id,
            blocks=[
                ParsedBlock(
                    text=result.text,
                    block_type="image_ocr",
                    page_number=1,
                    metadata={
                        "extraction_method": result.extraction_method,
                        "quality_notes": result.quality_notes,
                    },
                )
            ],
            source="imageparser",
            base_metadata={
                "extraction_method": result.extraction_method,
                "quality_notes": result.quality_notes,
            },
        )

    async def _mark_failed(
        self,
        document: Document,
        document_version: DocumentVersion,
        exc: Exception,
        started: float,
    ) -> None:
        metadata = {
            **(document.metadata_ or {}),
            "image_ocr": {
                "status": "failed",
                "error": str(exc),
                "duration_ms": int((time.perf_counter() - started) * 1000),
                "vision_model": settings.VISION_LM_STUDIO_MODEL,
            },
        }
        document.processing_status = DocumentProcessingStatus.FAILED
        document.text_extract_status = TextExtractStatus.FAILED
        document.metadata_ = metadata
        document_version.processing_status = DocumentProcessingStatus.FAILED
        document_version.text_extract_status = TextExtractStatus.FAILED
        document_version.metadata_ = {**(document_version.metadata_ or {}), "image_ocr": metadata["image_ocr"]}
        await self.db.flush()

    def _merge_metadata(self, current: dict | None, result: ImageParseResult) -> dict:
        return {
            **(current or {}),
            "requires_ocr": True,
            "image_ocr": {
                "status": "completed",
                "extraction_method": result.extraction_method,
                "characters_count": result.characters_count,
                "duration_ms": result.duration_ms,
                "vision_model": settings.VISION_LM_STUDIO_MODEL,
                "quality_notes": result.quality_notes,
            },
        }
