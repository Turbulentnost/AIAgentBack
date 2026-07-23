"""Async-сервис парсинга КД с интеграцией в document_processing."""

from __future__ import annotations

import time
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.kd_parse import KDParseResult
from app.services.document_processing.concurrency import run_blocking_document_task
from app.services.document_processing.kd.parser import KDParser, KDParserError
from app.services.document_processing.kd.pdf import merge_kd_into_pdf_metadata


class KDParsingService:
    """Парсинг КД из байтов или MinIO-объекта.

    Не дублирует PdfParsingService: работает с зонами и ESKD-метаданными.
    Для полного OCR pipeline передайте ``ocr_page`` или вызовите PdfParsingService
    после KD-парсинга для страниц с ``requires_ocr=True``.
    """

    def __init__(self, db: AsyncSession | None = None) -> None:
        self.db = db
        self._parser = KDParser()

    async def parse_bytes(
        self,
        data: bytes,
        *,
        filename: str | None = None,
        content_type: str | None = None,
        page_numbers: list[int] | None = None,
    ) -> KDParseResult:
        started = time.perf_counter()
        result = await run_blocking_document_task(
            self._parser.parse_bytes,
            data,
            filename=filename,
            content_type=content_type,
            page_numbers=page_numbers,
        )
        if result.duration_ms == 0:
            result.duration_ms = int((time.perf_counter() - started) * 1000)
        return result

    async def parse_path(
        self,
        path: Path | str,
        *,
        page_numbers: list[int] | None = None,
    ) -> KDParseResult:
        started = time.perf_counter()
        result = await run_blocking_document_task(
            self._parser.parse_path,
            path,
            page_numbers=page_numbers,
        )
        if result.duration_ms == 0:
            result.duration_ms = int((time.perf_counter() - started) * 1000)
        return result

    async def parse_minio_object(
        self,
        object_name: str,
        *,
        filename: str | None = None,
        content_type: str | None = None,
        page_numbers: list[int] | None = None,
    ) -> KDParseResult:
        from app.documents.storage import object_storage

        data = await run_blocking_document_task(object_storage.get_object, object_name)
        return await self.parse_bytes(
            data,
            filename=filename,
            content_type=content_type,
            page_numbers=page_numbers,
        )

    @staticmethod
    def enrich_document_metadata(
        current_metadata: dict | None,
        result: KDParseResult,
    ) -> dict:
        """Добавляет kd_parsing в metadata документа без изменения PdfParseResult."""
        return merge_kd_into_pdf_metadata(current_metadata, result)

    @staticmethod
    def eskd_fields_from_result(result: KDParseResult) -> dict[str, str | None]:
        """Поля для EskdValidationContext из результата KD-парсинга."""
        eskd_meta = result.metadata.get("eskd") if isinstance(result.metadata, dict) else None
        designation = None
        if isinstance(eskd_meta, dict):
            designation = eskd_meta.get("designation")
        if not designation and isinstance(result.metadata, dict):
            raw = result.metadata.get("detected_designation")
            designation = str(raw) if raw else None
        return {
            "document_text": result.eskd_document_text,
            "designation": designation,
        }
