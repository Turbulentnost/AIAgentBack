from __future__ import annotations

import io
import json
import time
import uuid
from dataclasses import dataclass
from typing import Any

from docx import Document as DocxDocument
from docx.oxml.table import CT_Tbl
from docx.oxml.text.paragraph import CT_P
from docx.table import Table
from docx.text.paragraph import Paragraph
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.documents.storage import object_storage
from app.models.document import Document, DocumentVersion
from app.models.enums import DocumentProcessingStatus, TextExtractStatus
from app.services.document_processing.chunking import DocumentChunkingService, ParsedBlock


class DocxParsingError(RuntimeError):
    pass


@dataclass
class DocxBlock:
    block_index: int
    block_type: str
    text: str
    style: str | None = None
    section_title: str | None = None
    table_index: int | None = None
    rows: list[list[str]] | None = None


@dataclass
class DocxParseResult:
    document_id: uuid.UUID
    document_version_id: uuid.UUID
    text: str
    blocks: list[DocxBlock]
    extracted_text_object_name: str
    extraction_method: str
    characters_count: int
    sections_count: int
    tables_count: int
    images_count: int
    duration_ms: int


class DocxParsingService:
    """Parse DOCX documents stored in MinIO and persist structured text for RAG."""

    SUPPORTED_CONTENT_TYPES = {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/msword",
    }

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def parse_document(
        self,
        *,
        document_id: uuid.UUID | None = None,
        document_version_id: uuid.UUID | None = None,
    ) -> DocxParseResult:
        started = time.perf_counter()
        document, document_version = await self._load_document(
            document_id=document_id,
            document_version_id=document_version_id,
        )

        content_type = document.content_type or document_version.content_type
        if not document.object_name:
            raise DocxParsingError("У документа нет object_name MinIO")
        if content_type not in self.SUPPORTED_CONTENT_TYPES:
            raise DocxParsingError(f"Документ не является поддерживаемым DOCX: {content_type}")

        try:
            docx_data = object_storage.get_object(document.object_name)
            blocks, images_count = self._extract_blocks(docx_data)
            full_text = self._build_full_text(blocks)
            extracted_text_object_name = self._save_extraction_result(
                document=document,
                document_version=document_version,
                blocks=blocks,
                full_text=full_text,
                images_count=images_count,
            )
            result = DocxParseResult(
                document_id=document.id,
                document_version_id=document_version.id,
                text=full_text,
                blocks=blocks,
                extracted_text_object_name=extracted_text_object_name,
                extraction_method="python_docx",
                characters_count=len(full_text),
                sections_count=sum(1 for block in blocks if block.block_type == "heading"),
                tables_count=sum(1 for block in blocks if block.block_type == "table"),
                images_count=images_count,
                duration_ms=int((time.perf_counter() - started) * 1000),
            )
            await self._persist_result(document, document_version, result)
            return result
        except Exception as exc:
            await self._mark_failed(document, document_version, exc, started)
            if isinstance(exc, DocxParsingError):
                raise
            raise DocxParsingError("Не удалось обработать DOCX-документ") from exc

    async def _load_document(
        self,
        *,
        document_id: uuid.UUID | None,
        document_version_id: uuid.UUID | None,
    ) -> tuple[Document, DocumentVersion]:
        if document_version_id:
            document_version = await self.db.get(DocumentVersion, document_version_id)
            if not document_version:
                raise DocxParsingError("Версия документа не найдена")
            document = await self.db.get(Document, document_version.document_id)
            if not document:
                raise DocxParsingError("Документ не найден")
            return document, document_version

        if not document_id:
            raise DocxParsingError("Нужно передать document_id или document_version_id")

        document = await self.db.get(Document, document_id)
        if not document:
            raise DocxParsingError("Документ не найден")

        result = await self.db.execute(
            select(DocumentVersion)
            .where(DocumentVersion.document_id == document.id)
            .order_by(DocumentVersion.is_current.desc(), DocumentVersion.version_number.desc())
            .limit(1)
        )
        document_version = result.scalar_one_or_none()
        if not document_version:
            raise DocxParsingError("У документа нет версий")
        return document, document_version

    def _extract_blocks(self, docx_data: bytes) -> tuple[list[DocxBlock], int]:
        document = DocxDocument(io.BytesIO(docx_data))
        blocks: list[DocxBlock] = []
        table_index = 0
        current_section: str | None = None

        for element in document.element.body.iterchildren():
            if isinstance(element, CT_P):
                paragraph = Paragraph(element, document)
                text = paragraph.text.strip()
                if not text:
                    continue
                style_name = paragraph.style.name if paragraph.style else None
                block_type = self._paragraph_type(paragraph)
                if block_type == "heading":
                    current_section = text
                blocks.append(
                    DocxBlock(
                        block_index=len(blocks),
                        block_type=block_type,
                        text=text,
                        style=style_name,
                        section_title=current_section,
                    )
                )
            elif isinstance(element, CT_Tbl):
                table_index += 1
                table = Table(element, document)
                rows = self._table_rows(table)
                table_text = self._table_to_text(table_index, rows)
                blocks.append(
                    DocxBlock(
                        block_index=len(blocks),
                        block_type="table",
                        text=table_text,
                        section_title=current_section,
                        table_index=table_index,
                        rows=rows,
                    )
                )

        images_count = self._count_images(document)
        return blocks, images_count

    def _paragraph_type(self, paragraph: Paragraph) -> str:
        style_name = (paragraph.style.name if paragraph.style else "").lower()
        if style_name.startswith("heading") or style_name.startswith("заголовок"):
            return "heading"
        if "list" in style_name or "спис" in style_name:
            return "list_item"
        return "paragraph"

    def _table_rows(self, table: Table) -> list[list[str]]:
        rows: list[list[str]] = []
        for row in table.rows:
            rows.append([cell.text.strip() for cell in row.cells])
        return rows

    def _table_to_text(self, table_index: int, rows: list[list[str]]) -> str:
        lines = [f"Таблица {table_index}"]
        for row in rows:
            lines.append(" | ".join(cell or "" for cell in row))
        return "\n".join(lines)

    def _count_images(self, document: DocxDocument) -> int:
        image_relationships = [
            rel
            for rel in document.part.rels.values()
            if "image" in rel.reltype
        ]
        inline_shapes_count = len(document.inline_shapes)
        return max(len(image_relationships), inline_shapes_count)

    def _build_full_text(self, blocks: list[DocxBlock]) -> str:
        return "\n\n".join(block.text for block in blocks if block.text.strip())

    def _save_extraction_result(
        self,
        *,
        document: Document,
        document_version: DocumentVersion,
        blocks: list[DocxBlock],
        full_text: str,
        images_count: int,
    ) -> str:
        object_name = f"documents/{document.id}/extracted_text/docx_v{document_version.version_number}.json"
        payload = {
            "document_id": str(document.id),
            "document_version_id": str(document_version.id),
            "text": full_text,
            "blocks": [self._block_to_dict(block) for block in blocks],
            "images_count": images_count,
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
        result: DocxParseResult,
    ) -> None:
        document.pages_count = None
        document.processing_status = DocumentProcessingStatus.TEXT_EXTRACTED
        document.text_extract_status = TextExtractStatus.EXTRACTED
        document.extracted_text_object_name = result.extracted_text_object_name
        document.metadata_ = self._merge_metadata(document.metadata_, result)

        document_version.pages_count = None
        document_version.processing_status = DocumentProcessingStatus.TEXT_EXTRACTED
        document_version.text_extract_status = TextExtractStatus.EXTRACTED
        document_version.extracted_text_object_name = result.extracted_text_object_name
        document_version.metadata_ = self._merge_metadata(document_version.metadata_, result)

        await DocumentChunkingService(self.db).replace_chunks(
            document_id=document.id,
            document_version_id=document_version.id,
            blocks=[
                ParsedBlock(
                    text=block.text,
                    block_type=block.block_type,
                    section_title=block.section_title,
                    metadata={
                        "block_index": block.block_index,
                        "block_type": block.block_type,
                        "style": block.style,
                        "section_title": block.section_title,
                        "table_index": block.table_index,
                        "rows": block.rows,
                    },
                )
                for block in result.blocks
            ],
            source="docx_parser",
            base_metadata={
                "extraction_method": result.extraction_method,
                "images_count": result.images_count,
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
            "docx_parsing": {
                "status": "failed",
                "error": str(exc),
                "duration_ms": int((time.perf_counter() - started) * 1000),
            },
        }
        document.processing_status = DocumentProcessingStatus.FAILED
        document.text_extract_status = TextExtractStatus.FAILED
        document.metadata_ = metadata
        document_version.processing_status = DocumentProcessingStatus.FAILED
        document_version.text_extract_status = TextExtractStatus.FAILED
        document_version.metadata_ = {
            **(document_version.metadata_ or {}),
            "docx_parsing": metadata["docx_parsing"],
        }
        await self.db.flush()

    def _merge_metadata(self, current: dict | None, result: DocxParseResult) -> dict:
        return {
            **(current or {}),
            "has_embedded_images": result.images_count > 0,
            "docx_parsing": {
                "status": "completed",
                "extraction_method": result.extraction_method,
                "characters_count": result.characters_count,
                "sections_count": result.sections_count,
                "tables_count": result.tables_count,
                "images_count": result.images_count,
                "duration_ms": result.duration_ms,
            },
        }

    def _block_to_dict(self, block: DocxBlock) -> dict[str, Any]:
        return {
            "block_index": block.block_index,
            "block_type": block.block_type,
            "text": block.text,
            "style": block.style,
            "section_title": block.section_title,
            "table_index": block.table_index,
            "rows": block.rows,
        }
