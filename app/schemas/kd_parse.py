"""Схемы результата парсинга конструкторской документации (КД)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


KDSourceFormat = Literal["pdf", "dxf", "dwg"]
KDRegionKind = Literal["title_block", "drawing", "full_page"]
KDTextMethod = Literal["pymupdf", "vision_ocr", "dxf", "none", "pending_ocr"]


class KDRegionBounds(BaseModel):
    x0: float
    y0: float
    x1: float
    y1: float


class KDRegionText(BaseModel):
    """Текст, извлечённый из зоны страницы КД."""

    region: KDRegionKind
    text: str = ""
    method: KDTextMethod = "none"
    char_count: int = 0
    bbox: KDRegionBounds | None = None


class KDPageResult(BaseModel):
    """Результат разбора одной страницы КД."""

    page_number: int
    width: float
    height: float
    is_scan: bool = False
    requires_ocr: bool = False
    title_block: KDRegionText
    drawing: KDRegionText
    scheme_png_base64: str | None = Field(
        default=None,
        description="PNG-снимок зоны чертежа (base64), без alpha",
    )
    eskd_text: str = Field(
        default="",
        description="Текст для проверок ЕСКД (основная надпись + реквизиты страницы)",
    )


class KDParseResult(BaseModel):
    """Структурированный результат парсинга файла КД."""

    source_format: KDSourceFormat
    source_filename: str | None = None
    pages_count: int = 0
    pages: list[KDPageResult] = Field(default_factory=list)
    eskd_document_text: str = Field(
        default="",
        description="Сводный текст для EskdValidationContext.document_text",
    )
    requires_ocr: bool = False
    ocr_used: bool = False
    duration_ms: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_eskd_metadata(self) -> dict[str, Any]:
        """Метаданные для сохранения в document.metadata_['kd_parsing']."""
        return {
            "source_format": self.source_format,
            "pages_count": self.pages_count,
            "requires_ocr": self.requires_ocr,
            "ocr_used": self.ocr_used,
            "duration_ms": self.duration_ms,
            **self.metadata,
        }
