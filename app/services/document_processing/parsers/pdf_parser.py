from __future__ import annotations

import base64
import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import fitz
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
from app.services.document_processing.parsers.vision_json import parse_pdf_vision_response


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
    text_blocks: list[dict[str, Any]] = field(default_factory=list)
    tables: list[dict[str, Any]] = field(default_factory=list)


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
    OCR_BATCH_SIZE = 1
    VISION_MAX_TOKENS = 12288

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
            pdf_data = await run_blocking_document_task(object_storage.get_object, document.object_name)
            pages, page_images = await run_blocking_document_task(self._extract_pages, pdf_data)
            pages_requiring_ocr = [
                page.page_number
                for page in pages
                if force_ocr or page.requires_ocr
            ]

            if pages_requiring_ocr:
                ocr_result_by_page = await self._extract_with_vision(page_images, pages_requiring_ocr)
                pages = [
                    self._merge_ocr_result(page, ocr_result_by_page.get(page.page_number))
                    for page in pages
                ]

            full_text = "\n\n".join(
                f"--- PAGE {page.page_number} ---\n{page.text.strip()}"
                for page in pages
                if page.text.strip()
            )
            extracted_text_object_name = await run_blocking_document_task(
                self._save_extraction_result,
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
                tables = self._extract_page_tables(page)
                text_blocks = self._extract_page_text_blocks(page, tables)
                ordered_text = "\n\n".join(block["text"] for block in text_blocks if block.get("text"))
                text = ordered_text.strip() or page.get_text("text").strip()
                has_images = bool(page.get_images(full=True))
                requires_ocr = (len(text) < self.MIN_TEXT_CHARS_PER_PAGE and not tables) or (
                    has_images and len(text) < 200 and not tables
                )
                pages.append(
                    PageParseResult(
                        page_number=page_index,
                        text=text,
                        method="pymupdf",
                        char_count=len(text),
                        has_images=has_images,
                        requires_ocr=requires_ocr,
                        text_blocks=text_blocks,
                        tables=tables,
                    )
                )
                pixmap = page.get_pixmap(matrix=fitz.Matrix(3, 3), alpha=False)
                page_images[page_index] = pixmap.tobytes("png")
        return pages, page_images

    def _extract_page_tables(self, page: "fitz.Page") -> list[dict[str, Any]]:
        try:
            finder = page.find_tables()
        except Exception:
            return []

        result: list[dict[str, Any]] = []
        for table_index, table in enumerate(getattr(finder, "tables", []) or []):
            try:
                rows_raw = table.extract() or []
            except Exception:
                continue
            rows = [
                ["" if cell is None else str(cell).strip() for cell in row]
                for row in rows_raw
            ]
            rows = [row for row in rows if any(cell for cell in row)]
            if not rows:
                continue
            bbox = getattr(table, "bbox", None)
            try:
                bbox_tuple = tuple(float(value) for value in bbox) if bbox is not None else None
            except Exception:
                bbox_tuple = None
            result.append(
                {
                    "table_index": table_index,
                    "bbox": bbox_tuple,
                    "rows": rows,
                }
            )
        return result

    def _extract_page_text_blocks(
        self,
        page: "fitz.Page",
        tables: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        try:
            raw_blocks = page.get_text("blocks") or []
        except Exception:
            raw_blocks = []

        table_rects = [table.get("bbox") for table in tables if table.get("bbox")]
        normalized: list[tuple[tuple[float, float, float, float], str]] = []
        for block in raw_blocks:
            if len(block) < 6:
                continue
            x0, y0, x1, y1, text = block[0], block[1], block[2], block[3], block[4]
            block_type = block[6] if len(block) >= 7 else 0
            if block_type != 0:
                continue
            if not isinstance(text, str) or not text.strip():
                continue
            rect = (float(x0), float(y0), float(x1), float(y1))
            if any(self._rect_inside(rect, table_rect) for table_rect in table_rects):
                continue
            normalized.append((rect, text.strip()))

        normalized.sort(key=lambda item: (round(item[0][1] / 5), round(item[0][0] / 5)))

        return [
            {"bbox": list(rect), "text": text}
            for rect, text in normalized
        ]

    @staticmethod
    def _rect_inside(inner: tuple[float, float, float, float], outer: tuple[float, ...] | None) -> bool:
        if not outer or len(outer) < 4:
            return False
        ox0, oy0, ox1, oy1 = outer[0], outer[1], outer[2], outer[3]
        ix0, iy0, ix1, iy1 = inner
        cx, cy = (ix0 + ix1) / 2, (iy0 + iy1) / 2
        return ox0 - 1 <= cx <= ox1 + 1 and oy0 - 1 <= cy <= oy1 + 1

    async def _extract_with_vision(
        self,
        page_images: dict[int, bytes],
        page_numbers: list[int],
    ) -> dict[int, dict[str, Any]]:
        result_by_page: dict[int, dict[str, Any]] = {}
        max_attempts = 3
        for page_number in page_numbers:
            last_error = "OCR не вернул текст"
            for attempt in range(max_attempts):
                try:
                    messages = self._build_vision_messages([page_number], page_images)
                    parsed_batch = await self._extract_vision_batch(messages, [page_number])
                    page_result = parsed_batch.get(page_number)
                    if page_result is None and parsed_batch:
                        page_result = next(iter(parsed_batch.values()))
                    if page_result and not page_result.get("error"):
                        has_content = bool(
                            str(page_result.get("text") or "").strip()
                            or page_result.get("text_blocks")
                            or page_result.get("tables")
                        )
                        if has_content:
                            result_by_page[page_number] = page_result
                            break
                        last_error = "OCR не вернул текст"
                    elif page_result and page_result.get("error"):
                        last_error = str(page_result["error"])
                except Exception as exc:
                    last_error = str(exc)
            else:
                result_by_page[page_number] = {
                    "text": "",
                    "text_blocks": [],
                    "tables": [],
                    "error": last_error,
                }
        return result_by_page

    async def _extract_vision_batch(
        self,
        messages: list[dict[str, Any]],
        batch: list[int],
    ) -> dict[int, dict[str, Any]]:
        response = await run_async_document_task(lambda: self._call_vision_model(messages))
        return self._parse_vision_response(response, batch)

    def _build_vision_messages(
        self,
        page_numbers: list[int],
        page_images: dict[int, bytes],
    ) -> list[dict[str, Any]]:
        content: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": (
                    "Извлеки текст и таблицы со страниц PDF в естественном порядке чтения. "
                    "Верни строго JSON-объект формата: {\"pages\": [{\"page_number\": 1, "
                    "\"text_blocks\": [\"абзац 1\", \"абзац 2\"], "
                    "\"tables\": [{\"caption\": \"...\", \"headers\": [\"Колонка 1\", \"Колонка 2\"], "
                    "\"rows\": [[\"a\", \"b\"], [\"c\", \"d\"]]}], "
                    "\"quality_notes\": \"\"}]}. "
                    "text_blocks — связные абзацы и заголовки в порядке чтения, без таблиц. "
                    "tables — все таблицы со страницы; первая строка должна быть заголовком, "
                    "остальные — данными. Не добавляй пояснения вне JSON, не используй markdown."
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
            "max_tokens": self.VISION_MAX_TOKENS,
        }
        async with httpx.AsyncClient(timeout=settings.VISION_OCR_TIMEOUT_SECONDS) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()
            data = response.json()
        return str(data["choices"][0]["message"]["content"])

    def _parse_vision_response(self, response: str, page_numbers: list[int]) -> dict[int, dict[str, Any]]:
        return parse_pdf_vision_response(response, page_numbers)

    @staticmethod
    def _normalize_vision_text_blocks(page: dict[str, Any]) -> list[str]:
        blocks_raw = page.get("text_blocks")
        if isinstance(blocks_raw, list):
            blocks = [str(item).strip() for item in blocks_raw if str(item).strip()]
            if blocks:
                return blocks
        text_raw = page.get("text")
        if isinstance(text_raw, str) and text_raw.strip():
            return [paragraph.strip() for paragraph in text_raw.split("\n\n") if paragraph.strip()]
        return []

    @staticmethod
    def _normalize_vision_tables(tables_raw: Any) -> list[dict[str, Any]]:
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

    def _merge_ocr_result(
        self,
        page: PageParseResult,
        ocr_data: dict[str, Any] | None,
    ) -> PageParseResult:
        if not ocr_data or ocr_data.get("error"):
            error = None
            if isinstance(ocr_data, dict) and ocr_data.get("error"):
                error = str(ocr_data["error"])
            elif page.requires_ocr:
                error = page.error or "OCR не вернул текст"
            if page.text.strip() or page.text_blocks or page.tables:
                return PageParseResult(
                    **{
                        **page.__dict__,
                        "error": error,
                    }
                )
            return PageParseResult(
                **{
                    **page.__dict__,
                    "error": error or "OCR не вернул текст",
                }
            )
        text_blocks_raw = ocr_data.get("text_blocks") or []
        text_blocks = [
            {"text": str(block).strip(), "bbox": None}
            for block in text_blocks_raw
            if isinstance(block, str) and block.strip()
        ]
        tables = ocr_data.get("tables") or []
        ocr_text = str(ocr_data.get("text") or "").strip()
        if not ocr_text and not text_blocks and not tables:
            return PageParseResult(
                **{**page.__dict__, "error": page.error or "OCR не вернул текст"}
            )
        return PageParseResult(
            page_number=page.page_number,
            text=ocr_text or "\n\n".join(block["text"] for block in text_blocks),
            method="vision_ocr",
            char_count=len(ocr_text),
            has_images=page.has_images,
            requires_ocr=True,
            error=None,
            text_blocks=text_blocks,
            tables=tables,
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
            "pages": [self._page_to_payload(page) for page in pages],
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

        await DocumentChunkingService(self.db).replace_chunks(
            document_id=document.id,
            document_version_id=document_version.id,
            blocks=self._build_chunking_blocks(result.pages),
            source="pdf_parser",
            base_metadata={
                "extraction_method": result.extraction_method,
                "requires_ocr": result.requires_ocr,
                "ocr_used": result.ocr_used,
            },
        )

    def _build_chunking_blocks(self, pages: list[PageParseResult]) -> list[ParsedBlock]:
        blocks: list[ParsedBlock] = []
        for page in pages:
            page_meta_common = {
                "page_number": page.page_number,
                "method": page.method,
                "has_images": page.has_images,
                "requires_ocr": page.requires_ocr,
                "error": page.error,
            }
            text_blocks = page.text_blocks or []
            tables = page.tables or []

            if not text_blocks and not tables and page.text.strip():
                blocks.append(
                    ParsedBlock(
                        text=page.text.strip(),
                        block_type="page",
                        page_number=page.page_number,
                        metadata={**page_meta_common},
                    )
                )
                continue

            ordered: list[tuple[float, int, ParsedBlock]] = []

            for index, text_block in enumerate(text_blocks):
                text = str(text_block.get("text", "")).strip()
                if not text:
                    continue
                bbox = text_block.get("bbox")
                sort_key = float(bbox[1]) if isinstance(bbox, (list, tuple)) and len(bbox) >= 2 else float(index)
                ordered.append(
                    (
                        sort_key,
                        index,
                        ParsedBlock(
                            text=text,
                            block_type="paragraph",
                            page_number=page.page_number,
                            metadata={
                                **page_meta_common,
                                "block_index": index,
                                "bbox": bbox,
                            },
                        ),
                    )
                )

            for table_index, table in enumerate(tables):
                rows = table.get("rows") or []
                if not rows:
                    continue
                bbox = table.get("bbox")
                sort_key = (
                    float(bbox[1])
                    if isinstance(bbox, (list, tuple)) and len(bbox) >= 2
                    else float(len(text_blocks) + table_index)
                )
                ordered.append(
                    (
                        sort_key,
                        len(text_blocks) + table_index,
                        ParsedBlock(
                            text="\n".join(" | ".join(row) for row in rows),
                            block_type="table",
                            page_number=page.page_number,
                            section_title=table.get("caption") or None,
                            metadata={
                                **page_meta_common,
                                "table_index": table.get("table_index", table_index),
                                "table_caption": table.get("caption"),
                                "rows": rows,
                                "bbox": bbox,
                            },
                        ),
                    )
                )

            ordered.sort(key=lambda item: (item[0], item[1]))
            blocks.extend(block for _, _, block in ordered)
        return blocks

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
        tables_count = sum(len(page.tables or []) for page in result.pages)
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
                "tables_count": tables_count,
                "duration_ms": result.duration_ms,
                "vision_model": settings.VISION_LM_STUDIO_MODEL if result.ocr_used else None,
            },
        }

    @staticmethod
    def _page_to_payload(page: PageParseResult) -> dict[str, Any]:
        def _serialize(value: Any) -> Any:
            if isinstance(value, tuple):
                return [_serialize(item) for item in value]
            if isinstance(value, list):
                return [_serialize(item) for item in value]
            if isinstance(value, dict):
                return {key: _serialize(item) for key, item in value.items()}
            return value

        return {
            "page_number": page.page_number,
            "text": page.text,
            "method": page.method,
            "char_count": page.char_count,
            "has_images": page.has_images,
            "requires_ocr": page.requires_ocr,
            "error": page.error,
            "text_blocks": _serialize(page.text_blocks),
            "tables": _serialize(page.tables),
        }

    def _extraction_method(self, pages: list[PageParseResult]) -> str:
        methods = {page.method for page in pages if page.text.strip()}
        if "vision_ocr" in methods and "pymupdf" in methods:
            return "pymupdf+vision_ocr"
        if "vision_ocr" in methods:
            return "vision_ocr"
        return "pymupdf"
