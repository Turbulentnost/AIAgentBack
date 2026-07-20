from __future__ import annotations

import base64
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.documents.storage import object_storage
from app.models.document import Document, DocumentVersion
from app.models.enums import DocumentProcessingStatus, TextExtractStatus
from app.services.document_processing.chunking import DocumentChunkingService, ParsedBlock
from app.services.document_processing.concurrency import (
    run_async_document_task,
    run_blocking_document_task,
)
from app.services.document_processing.parsers.vision_json import parse_image_vision_response


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
    text_blocks: list[str] = field(default_factory=list)
    tables: list[dict[str, Any]] = field(default_factory=list)


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
            image_data = await run_blocking_document_task(object_storage.get_object, document.object_name)
            response = await self._extract_with_vision(image_data, content_type)
            text, quality_notes, text_blocks, tables = self._parse_vision_response(response)
            extracted_text_object_name = await run_blocking_document_task(
                self._save_extraction_result,
                document=document,
                document_version=document_version,
                text=text,
                text_blocks=text_blocks,
                tables=tables,
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
                text_blocks=text_blocks,
                tables=tables,
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
                                "Извлеки текст и таблицы с изображения документа в естественном порядке чтения. "
                                "Верни строго JSON-объект формата: "
                                "{\"text_blocks\": [\"абзац 1\", \"абзац 2\"], "
                                "\"tables\": [{\"caption\": \"\", \"headers\": [\"Колонка 1\"], "
                                "\"rows\": [[\"a\"], [\"b\"]]}], "
                                "\"quality_notes\": \"\"}. "
                                "text_blocks — связные абзацы, заголовки и реквизиты в порядке чтения, без таблиц. "
                                "tables — все таблицы; первая строка должна быть заголовком, остальные — данными. "
                                "Не добавляй пояснения вне JSON, не используй markdown."
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
            "max_tokens": 12288,
        }
        async def _request() -> dict[str, Any]:
            async with httpx.AsyncClient(timeout=settings.VISION_OCR_TIMEOUT_SECONDS) as client:
                response = await client.post(url, json=payload)
                response.raise_for_status()
                return response.json()

        data = await run_async_document_task(_request)
        return str(data["choices"][0]["message"]["content"])

    def _parse_vision_response(
        self,
        response: str,
    ) -> tuple[str, str | None, list[str], list[dict[str, Any]]]:
        return parse_image_vision_response(response)

    @staticmethod
    def _normalize_text_blocks(payload: dict[str, Any]) -> list[str]:
        raw = payload.get("text_blocks")
        if isinstance(raw, list):
            blocks = [str(item).strip() for item in raw if str(item).strip()]
            if blocks:
                return blocks
        text_raw = payload.get("text")
        if isinstance(text_raw, str) and text_raw.strip():
            return [paragraph.strip() for paragraph in text_raw.split("\n\n") if paragraph.strip()]
        return []

    @staticmethod
    def _normalize_tables(tables_raw: Any) -> list[dict[str, Any]]:
        if not isinstance(tables_raw, list):
            return []
        normalized: list[dict[str, Any]] = []
        for table_index, table in enumerate(tables_raw):
            if not isinstance(table, dict):
                continue
            rows_input = table.get("rows")
            if not isinstance(rows_input, list):
                continue
            rows: list[list[str]] = []
            for row in rows_input:
                if not isinstance(row, list):
                    continue
                rows.append(["" if cell is None else str(cell).strip() for cell in row])
            rows = [row for row in rows if any(cell for cell in row)]
            if not rows:
                continue
            headers_input = table.get("headers")
            if isinstance(headers_input, list) and headers_input:
                headers = [str(item).strip() for item in headers_input]
                rows = [headers] + rows
            caption = table.get("caption")
            normalized.append(
                {
                    "table_index": table_index,
                    "rows": rows,
                    "caption": str(caption).strip() if isinstance(caption, str) and caption.strip() else None,
                }
            )
        return normalized

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
        text_blocks: list[str],
        tables: list[dict[str, Any]],
        quality_notes: str | None,
        content_type: str,
    ) -> str:
        object_name = f"documents/{document.id}/extracted_text/image_v{document_version.version_number}.json"
        payload = {
            "document_id": str(document.id),
            "document_version_id": str(document_version.id),
            "content_type": content_type,
            "text": text,
            "text_blocks": text_blocks,
            "tables": tables,
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
            blocks=self._build_chunking_blocks(result),
            source="imageparser",
            base_metadata={
                "extraction_method": result.extraction_method,
                "quality_notes": result.quality_notes,
            },
        )

    def _build_chunking_blocks(self, result: ImageParseResult) -> list[ParsedBlock]:
        blocks: list[ParsedBlock] = []
        common_meta = {
            "extraction_method": result.extraction_method,
            "quality_notes": result.quality_notes,
        }

        if not result.text_blocks and not result.tables:
            blocks.append(
                ParsedBlock(
                    text=result.text,
                    block_type="image_ocr",
                    page_number=1,
                    metadata=common_meta,
                )
            )
            return blocks

        for index, paragraph in enumerate(result.text_blocks):
            paragraph_text = paragraph.strip()
            if not paragraph_text:
                continue
            blocks.append(
                ParsedBlock(
                    text=paragraph_text,
                    block_type="paragraph",
                    page_number=1,
                    metadata={**common_meta, "block_index": index},
                )
            )

        for table_index, table in enumerate(result.tables):
            rows = table.get("rows") or []
            if not rows:
                continue
            blocks.append(
                ParsedBlock(
                    text="\n".join(" | ".join(row) for row in rows),
                    block_type="table",
                    page_number=1,
                    section_title=table.get("caption") or None,
                    metadata={
                        **common_meta,
                        "table_index": table.get("table_index", table_index),
                        "table_caption": table.get("caption"),
                        "rows": rows,
                    },
                )
            )

        if not blocks:
            blocks.append(
                ParsedBlock(
                    text=result.text,
                    block_type="image_ocr",
                    page_number=1,
                    metadata=common_meta,
                )
            )
        return blocks

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
                "tables_count": len(result.tables or []),
                "text_blocks_count": len(result.text_blocks or []),
                "duration_ms": result.duration_ms,
                "vision_model": settings.VISION_LM_STUDIO_MODEL,
                "quality_notes": result.quality_notes,
            },
        }
