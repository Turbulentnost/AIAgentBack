from __future__ import annotations

import base64
import json
import time
import uuid
from dataclasses import dataclass
from typing import Any

import fitz
import httpx
from sqlalchemy import select
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.documents.processor import chunk_text
from app.documents.storage import object_storage
from app.models.document import Document, DocumentChunk, DocumentVersion
from app.models.enums import DocumentProcessingStatus, TextExtractStatus


class PdfParsingError(RuntimeError):
    pass


@dataclass
class PageParseResult:
    page_number: int
    text: str
    method: str
    char_count: int
    has_images: bool
    requires_ocr: bool
    error: str | None = None


@dataclass
class PdfParseResult:
    document_id: uuid.UUID
    document_version_id: uuid.UUID
    pages_count: int
    text: str
    pages: list[PageParseResult]
    extracted_text_object_name: str
    extraction_method: str
    requires_ocr: bool
    ocr_used: bool
    characters_count: int
    failed_pages: list[int]
    duration_ms: int


class PdfParsingService:
    """Parse PDF documents stored in MinIO and persist extracted text metadata."""

    MIN_TEXT_CHARS_PER_PAGE = 40
    OCR_BATCH_SIZE = 2

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def parse_document(
        self,
        *,
        document_id: uuid.UUID | None = None,
        document_version_id: uuid.UUID | None = None,
        force_ocr: bool = False,
    ) -> PdfParseResult:
        started = time.perf_counter()
        document, document_version = await self._load_document(
            document_id=document_id,
            document_version_id=document_version_id,
        )

        if not document.object_name:
            raise PdfParsingError("У документа нет object_name MinIO")
        if document.content_type and "pdf" not in document.content_type:
            raise PdfParsingError(f"Документ не является PDF: {document.content_type}")

        try:
            pdf_data = object_storage.get_object(document.object_name)
            pages, page_images = self._extract_pages(pdf_data)
            pages_requiring_ocr = [
                page.page_number
                for page in pages
                if force_ocr or page.requires_ocr
            ]

            if pages_requiring_ocr:
                ocr_text_by_page = await self._extract_with_vision(page_images, pages_requiring_ocr)
                pages = [
                    self._merge_ocr_result(page, ocr_text_by_page.get(page.page_number))
                    for page in pages
                ]

            full_text = "\n\n".join(
                f"--- PAGE {page.page_number} ---\n{page.text.strip()}"
                for page in pages
                if page.text.strip()
            )
            extracted_text_object_name = self._save_extraction_result(
                document=document,
                document_version=document_version,
                pages=pages,
                full_text=full_text,
            )

            result = PdfParseResult(
                document_id=document.id,
                document_version_id=document_version.id,
                pages_count=len(pages),
                text=full_text,
                pages=pages,
                extracted_text_object_name=extracted_text_object_name,
                extraction_method=self._extraction_method(pages),
                requires_ocr=any(page.requires_ocr for page in pages),
                ocr_used=any(page.method == "vision_ocr" for page in pages),
                characters_count=len(full_text),
                failed_pages=[page.page_number for page in pages if page.error],
                duration_ms=int((time.perf_counter() - started) * 1000),
            )
            await self._persist_result(document, document_version, result)
            return result
        except Exception as exc:
            await self._mark_failed(document, document_version, exc, started)
            if isinstance(exc, PdfParsingError):
                raise
            raise PdfParsingError("Не удалось обработать PDF-документ") from exc

    async def _load_document(
        self,
        *,
        document_id: uuid.UUID | None,
        document_version_id: uuid.UUID | None,
    ) -> tuple[Document, DocumentVersion]:
        if document_version_id:
            document_version = await self.db.get(DocumentVersion, document_version_id)
            if not document_version:
                raise PdfParsingError("Версия документа не найдена")
            document = await self.db.get(Document, document_version.document_id)
            if not document:
                raise PdfParsingError("Документ не найден")
            return document, document_version

        if not document_id:
            raise PdfParsingError("Нужно передать document_id или document_version_id")

        document = await self.db.get(Document, document_id)
        if not document:
            raise PdfParsingError("Документ не найден")

        result = await self.db.execute(
            select(DocumentVersion)
            .where(DocumentVersion.document_id == document.id)
            .order_by(DocumentVersion.is_current.desc(), DocumentVersion.version_number.desc())
            .limit(1)
        )
        document_version = result.scalar_one_or_none()
        if not document_version:
            raise PdfParsingError("У документа нет версий")
        return document, document_version

    def _extract_pages(self, pdf_data: bytes) -> tuple[list[PageParseResult], dict[int, bytes]]:
        pages: list[PageParseResult] = []
        page_images: dict[int, bytes] = {}
        with fitz.open(stream=pdf_data, filetype="pdf") as pdf:
            for page_index, page in enumerate(pdf, start=1):
                text = page.get_text("text").strip()
                has_images = bool(page.get_images(full=True))
                requires_ocr = len(text) < self.MIN_TEXT_CHARS_PER_PAGE
                pages.append(
                    PageParseResult(
                        page_number=page_index,
                        text=text,
                        method="pymupdf",
                        char_count=len(text),
                        has_images=has_images,
                        requires_ocr=requires_ocr,
                    )
                )
                pixmap = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
                page_images[page_index] = pixmap.tobytes("png")
        return pages, page_images

    async def _extract_with_vision(
        self,
        page_images: dict[int, bytes],
        page_numbers: list[int],
    ) -> dict[int, str]:
        text_by_page: dict[int, str] = {}
        for offset in range(0, len(page_numbers), self.OCR_BATCH_SIZE):
            batch = page_numbers[offset : offset + self.OCR_BATCH_SIZE]
            messages = self._build_vision_messages(batch, page_images)
            response = await self._call_vision_model(messages)
            text_by_page.update(self._parse_vision_response(response, batch))
        return text_by_page

    def _build_vision_messages(
        self,
        page_numbers: list[int],
        page_images: dict[int, bytes],
    ) -> list[dict[str, Any]]:
        content: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": (
                    "Извлеки весь читаемый текст, таблицы и реквизиты с PDF-страниц. "
                    "Верни строго JSON-объект вида {\"pages\": [{\"page_number\": 1, "
                    "\"text\": \"...\", \"tables\": [], \"quality_notes\": \"...\"}]}. "
                    "Не добавляй пояснения вне JSON."
                ),
            }
        ]
        for page_number in page_numbers:
            image_base64 = base64.b64encode(page_images[page_number]).decode("ascii")
            content.append({"type": "text", "text": f"Страница {page_number}:"})
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{image_base64}"},
                }
            )
        return [{"role": "user", "content": content}]

    async def _call_vision_model(self, messages: list[dict[str, Any]]) -> str:
        url = f"{settings.VISION_LM_STUDIO_BASE_URL.rstrip('/')}/chat/completions"
        payload = {
            "model": settings.VISION_LM_STUDIO_MODEL,
            "messages": messages,
            "temperature": 0,
            "max_tokens": 4096,
        }
        async with httpx.AsyncClient(timeout=180) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()
            data = response.json()
        return str(data["choices"][0]["message"]["content"])

    def _parse_vision_response(self, response: str, page_numbers: list[int]) -> dict[int, str]:
        try:
            payload = json.loads(self._strip_code_fence(response))
            pages = payload.get("pages", [])
            parsed = {
                int(page["page_number"]): str(page.get("text", "")).strip()
                for page in pages
                if "page_number" in page
            }
            if parsed:
                return parsed
        except Exception:
            pass

        if len(page_numbers) == 1:
            return {page_numbers[0]: response.strip()}
        return {page_numbers[0]: response.strip()}

    def _strip_code_fence(self, value: str) -> str:
        stripped = value.strip()
        if stripped.startswith("```"):
            stripped = stripped.split("\n", 1)[-1]
            stripped = stripped.rsplit("```", 1)[0]
        return stripped.strip()

    def _merge_ocr_result(self, page: PageParseResult, ocr_text: str | None) -> PageParseResult:
        if not ocr_text:
            return PageParseResult(
                **{**page.__dict__, "error": page.error or "OCR не вернул текст"}
            )
        return PageParseResult(
            page_number=page.page_number,
            text=ocr_text,
            method="vision_ocr",
            char_count=len(ocr_text),
            has_images=page.has_images,
            requires_ocr=True,
            error=None,
        )

    def _save_extraction_result(
        self,
        *,
        document: Document,
        document_version: DocumentVersion,
        pages: list[PageParseResult],
        full_text: str,
    ) -> str:
        object_name = f"documents/{document.id}/extracted_text/v{document_version.version_number}.json"
        payload = {
            "document_id": str(document.id),
            "document_version_id": str(document_version.id),
            "text": full_text,
            "pages": [page.__dict__ for page in pages],
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
        result: PdfParseResult,
    ) -> None:
        metadata = self._merge_metadata(document.metadata_, result)
        document.pages_count = result.pages_count
        document.processing_status = (
            DocumentProcessingStatus.FAILED
            if result.failed_pages and not result.text.strip()
            else DocumentProcessingStatus.TEXT_EXTRACTED
        )
        document.text_extract_status = (
            TextExtractStatus.FAILED
            if result.failed_pages and not result.text.strip()
            else TextExtractStatus.EXTRACTED
        )
        document.extracted_text_object_name = result.extracted_text_object_name
        document.metadata_ = metadata

        document_version.pages_count = result.pages_count
        document_version.processing_status = document.processing_status
        document_version.text_extract_status = document.text_extract_status
        document_version.extracted_text_object_name = result.extracted_text_object_name
        document_version.metadata_ = self._merge_metadata(document_version.metadata_, result)

        await self.db.execute(delete(DocumentChunk).where(DocumentChunk.document_version_id == document_version.id))
        for index, chunk in enumerate(chunk_text(result.text)):
            self.db.add(
                DocumentChunk(
                    document_id=document.id,
                    document_version_id=document_version.id,
                    chunk_index=index,
                    text=chunk,
                    token_count=len(chunk.split()),
                    qdrant_collection=settings.QDRANT_COLLECTION,
                    embedding_model=settings.LLM_EMBEDDING_MODEL,
                    is_indexed=False,
                    metadata_={
                        "source": "pdf_parser",
                        "extraction_method": result.extraction_method,
                        "requires_ocr": result.requires_ocr,
                        "ocr_used": result.ocr_used,
                    },
                    content=chunk,
                    chunk_metadata={
                        "source": "pdf_parser",
                        "extraction_method": result.extraction_method,
                    },
                )
            )
        await self.db.flush()

    async def _mark_failed(
        self,
        document: Document,
        document_version: DocumentVersion,
        exc: Exception,
        started: float,
    ) -> None:
        error_metadata = {
            **(document.metadata_ or {}),
            "pdf_parsing": {
                "status": "failed",
                "error": str(exc),
                "duration_ms": int((time.perf_counter() - started) * 1000),
            },
        }
        document.processing_status = DocumentProcessingStatus.FAILED
        document.text_extract_status = TextExtractStatus.FAILED
        document.metadata_ = error_metadata
        document_version.processing_status = DocumentProcessingStatus.FAILED
        document_version.text_extract_status = TextExtractStatus.FAILED
        document_version.metadata_ = {**(document_version.metadata_ or {}), "pdf_parsing": error_metadata["pdf_parsing"]}
        await self.db.flush()

    def _merge_metadata(self, current: dict | None, result: PdfParseResult) -> dict:
        return {
            **(current or {}),
            "requires_ocr": result.requires_ocr,
            "pdf_parsing": {
                "status": "completed" if not result.failed_pages else "partial",
                "pages_count": result.pages_count,
                "extraction_method": result.extraction_method,
                "requires_ocr": result.requires_ocr,
                "ocr_used": result.ocr_used,
                "characters_count": result.characters_count,
                "failed_pages": result.failed_pages,
                "duration_ms": result.duration_ms,
                "vision_model": settings.VISION_LM_STUDIO_MODEL if result.ocr_used else None,
            },
        }

    def _extraction_method(self, pages: list[PageParseResult]) -> str:
        methods = {page.method for page in pages if page.text.strip()}
        if "vision_ocr" in methods and "pymupdf" in methods:
            return "pymupdf+vision_ocr"
        if "vision_ocr" in methods:
            return "vision_ocr"
        return "pymupdf"
