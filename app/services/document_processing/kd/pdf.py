"""Парсинг PDF-файлов КД (нативный текст и сканы)."""

from __future__ import annotations

import base64
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import fitz

from app.schemas.kd_parse import KDPageResult, KDRegionText, KDParseResult, KDTextMethod
from app.services.document_processing.kd.formats import detect_kd_format
from app.services.document_processing.kd.regions import (
    detect_page_regions,
    extract_region_text,
    page_requires_ocr,
    rect_to_bounds,
    render_region_png,
)


class KDPdfParsingError(RuntimeError):
    pass


OcrPageCallback = Callable[[int, bytes], str | None]


def parse_kd_pdf_bytes(
    data: bytes,
    *,
    filename: str | None = None,
    page_numbers: list[int] | None = None,
    render_scheme: bool = True,
    scheme_zoom: float = 3.0,
    ocr_page: OcrPageCallback | None = None,
) -> KDParseResult:
    """Разбирает PDF КД: зоны, текст штампа, PNG схемы.

    ``ocr_page(page_number, png_bytes) -> text`` — опциональный OCR-хук
    (например, vision-модель из PdfParsingService). Вызывается для страниц-сканов.
    """
    started = time.perf_counter()
    source_format = detect_kd_format(data, filename=filename, content_type="application/pdf")
    if source_format != "pdf":
        raise KDPdfParsingError(f"Ожидался PDF, получен {source_format}")

    pages: list[KDPageResult] = []
    ocr_used = False

    with fitz.open(stream=data, filetype="pdf") as pdf:
        selected = page_numbers or list(range(1, pdf.page_count + 1))
        for page_number in selected:
            if page_number < 1 or page_number > pdf.page_count:
                raise KDPdfParsingError(
                    f"Страница {page_number} вне диапазона 1..{pdf.page_count}"
                )
            page = pdf[page_number - 1]
            page_result = _parse_pdf_page(
                page,
                page_number=page_number,
                render_scheme=render_scheme,
                scheme_zoom=scheme_zoom,
                ocr_page=ocr_page,
            )
            if page_result.requires_ocr and page_result.title_block.method == "vision_ocr":
                ocr_used = True
            pages.append(page_result)

    eskd_text = _build_eskd_document_text(pages)
    duration_ms = int((time.perf_counter() - started) * 1000)
    return KDParseResult(
        source_format="pdf",
        source_filename=filename,
        pages_count=len(pages),
        pages=pages,
        eskd_document_text=eskd_text,
        requires_ocr=any(page.requires_ocr for page in pages),
        ocr_used=ocr_used,
        duration_ms=duration_ms,
        metadata={
            "scheme_zoom": scheme_zoom,
            "parsed_page_numbers": [page.page_number for page in pages],
        },
    )


def parse_kd_pdf_path(
    path: Path | str,
    *,
    page_numbers: list[int] | None = None,
    render_scheme: bool = True,
    scheme_zoom: float = 3.0,
    ocr_page: OcrPageCallback | None = None,
) -> KDParseResult:
    """Читает PDF с диска и делегирует в ``parse_kd_pdf_bytes``."""
    file_path = Path(path)
    data = file_path.read_bytes()
    return parse_kd_pdf_bytes(
        data,
        filename=file_path.name,
        page_numbers=page_numbers,
        render_scheme=render_scheme,
        scheme_zoom=scheme_zoom,
        ocr_page=ocr_page,
    )


def _parse_pdf_page(
    page: fitz.Page,
    *,
    page_number: int,
    render_scheme: bool,
    scheme_zoom: float,
    ocr_page: OcrPageCallback | None,
) -> KDPageResult:
    regions = detect_page_regions(page)
    requires_ocr = page_requires_ocr(page, title_block=regions.title_block)

    title_text = extract_region_text(page, regions.title_block)
    drawing_text = extract_region_text(page, regions.drawing)
    title_method: KDTextMethod = "pymupdf" if title_text else "none"

    scheme_png: bytes | None = None
    if render_scheme:
        scheme_png = render_region_png(page, regions.drawing, zoom=scheme_zoom)

    if requires_ocr and not title_text and ocr_page is not None and scheme_png is not None:
        # OCR по PNG штампа (выше разрешение для мелкого текста).
        stamp_png = render_region_png(page, regions.title_block, zoom=scheme_zoom)
        ocr_text = ocr_page(page_number, stamp_png)
        if ocr_text and ocr_text.strip():
            title_text = ocr_text.strip()
            title_method = "vision_ocr"

    if requires_ocr and not title_text:
        title_method = "pending_ocr"

    eskd_text = title_text or drawing_text
    return KDPageResult(
        page_number=page_number,
        width=regions.page_rect.width,
        height=regions.page_rect.height,
        is_scan=requires_ocr,
        requires_ocr=requires_ocr,
        title_block=KDRegionText(
            region="title_block",
            text=title_text,
            method=title_method,
            char_count=len(title_text),
            bbox=rect_to_bounds(regions.title_block),
        ),
        drawing=KDRegionText(
            region="drawing",
            text=drawing_text,
            method="pymupdf" if drawing_text else "none",
            char_count=len(drawing_text),
            bbox=rect_to_bounds(regions.drawing),
        ),
        scheme_png_base64=base64.b64encode(scheme_png).decode("ascii") if scheme_png else None,
        eskd_text=eskd_text,
    )


def _build_eskd_document_text(pages: list[KDPageResult]) -> str:
    parts: list[str] = []
    for page in pages:
        text = page.eskd_text.strip()
        if not text:
            continue
        if len(pages) == 1:
            parts.append(text)
        else:
            parts.append(f"--- PAGE {page.page_number} ---\n{text}")
    return "\n\n".join(parts)


def merge_kd_into_pdf_metadata(
    current_metadata: dict[str, Any] | None,
    result: KDParseResult,
) -> dict[str, Any]:
    """Объединяет результат KD-парсинга с metadata документа (для PdfParsingService)."""
    return {
        **(current_metadata or {}),
        "kd_parsing": result.to_eskd_metadata(),
        "requires_ocr": result.requires_ocr,
    }
