"""Оркестратор парсинга конструкторской документации (КД)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from app.schemas.kd_parse import KDSourceFormat, KDParseResult
from app.services.document_processing.kd.dxf import (
    KDDxfNotImplementedError,
    parse_kd_dwg_bytes,
    parse_kd_dxf_bytes,
)
from app.services.document_processing.kd.formats import KDFormatError, detect_kd_format
from app.services.document_processing.kd.pdf import OcrPageCallback, parse_kd_pdf_bytes, parse_kd_pdf_path


class KDParserError(RuntimeError):
    pass


class KDParser:
    """Единая точка входа: определение формата → парсер → KDParseResult."""

    def parse_bytes(
        self,
        data: bytes,
        *,
        filename: str | None = None,
        content_type: str | None = None,
        page_numbers: list[int] | None = None,
        render_scheme: bool = True,
        scheme_zoom: float = 3.0,
        ocr_page: OcrPageCallback | None = None,
    ) -> KDParseResult:
        try:
            source_format = detect_kd_format(data, filename=filename, content_type=content_type)
        except KDFormatError as exc:
            raise KDParserError(str(exc)) from exc

        return self._dispatch(
            source_format,
            data,
            filename=filename,
            page_numbers=page_numbers,
            render_scheme=render_scheme,
            scheme_zoom=scheme_zoom,
            ocr_page=ocr_page,
        )

    def parse_path(
        self,
        path: Path | str,
        *,
        page_numbers: list[int] | None = None,
        render_scheme: bool = True,
        scheme_zoom: float = 3.0,
        ocr_page: OcrPageCallback | None = None,
    ) -> KDParseResult:
        file_path = Path(path)
        data = file_path.read_bytes()
        return self.parse_bytes(
            data,
            filename=file_path.name,
            page_numbers=page_numbers,
            render_scheme=render_scheme,
            scheme_zoom=scheme_zoom,
            ocr_page=ocr_page,
        )

    @staticmethod
    def _dispatch(
        source_format: KDSourceFormat,
        data: bytes,
        *,
        filename: str | None,
        page_numbers: list[int] | None,
        render_scheme: bool,
        scheme_zoom: float,
        ocr_page: OcrPageCallback | None,
    ) -> KDParseResult:
        if source_format == "pdf":
            return parse_kd_pdf_bytes(
                data,
                filename=filename,
                page_numbers=page_numbers,
                render_scheme=render_scheme,
                scheme_zoom=scheme_zoom,
                ocr_page=ocr_page,
            )
        if source_format == "dxf":
            return parse_kd_dxf_bytes(
                data,
                filename=filename,
                render_scheme=render_scheme,
                scheme_zoom=scheme_zoom,
            )
        if source_format == "dwg":
            return parse_kd_dwg_bytes(data, filename=filename)
        raise KDParserError(f"Формат {source_format} не поддерживается")
