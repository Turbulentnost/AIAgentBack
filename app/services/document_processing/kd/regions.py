"""Эвристики зон КД: основная надпись (штамп) и поле чертежа по ГОСТ 2.104."""

from __future__ import annotations

from dataclasses import dataclass

import fitz

from app.schemas.kd_parse import KDRegionBounds


@dataclass(frozen=True)
class KDPageRegions:
    """Прямоугольники зон страницы КД в координатах PyMuPDF."""

    page_rect: fitz.Rect
    title_block: fitz.Rect
    drawing: fitz.Rect


# Доля страницы под штамп (ориентир — ландшафтный A4 из eskd-фикстур: 215×125 pt).
TITLE_BLOCK_WIDTH_RATIO = 0.255
TITLE_BLOCK_HEIGHT_RATIO = 0.21
MIN_TEXT_CHARS_FOR_NATIVE = 40


def detect_page_regions(page: fitz.Page) -> KDPageRegions:
    """Разбивает страницу на зону чертежа и основную надпись (нижний правый угол)."""
    rect = page.rect
    tb_width = rect.width * TITLE_BLOCK_WIDTH_RATIO
    tb_height = rect.height * TITLE_BLOCK_HEIGHT_RATIO
    title_block = fitz.Rect(rect.x1 - tb_width, rect.y1 - tb_height, rect.x1, rect.y1)
    drawing = fitz.Rect(rect.x0, rect.y0, title_block.x0, title_block.y0)
    return KDPageRegions(page_rect=rect, title_block=title_block, drawing=drawing)


def rect_to_bounds(rect: fitz.Rect) -> KDRegionBounds:
    return KDRegionBounds(x0=rect.x0, y0=rect.y0, x1=rect.x1, y1=rect.y1)


def extract_region_text(page: fitz.Page, clip: fitz.Rect) -> str:
    """Извлекает текст из прямоугольной области страницы через PyMuPDF."""
    return page.get_text("text", clip=clip).strip()


def page_requires_ocr(page: fitz.Page, *, title_block: fitz.Rect) -> bool:
    """Страница считается сканом, если в штампе мало извлекаемого текста."""
    title_text = extract_region_text(page, title_block)
    full_text = page.get_text("text").strip()
    has_images = bool(page.get_images(full=True))
    if len(title_text) >= MIN_TEXT_CHARS_FOR_NATIVE:
        return False
    if len(full_text) >= MIN_TEXT_CHARS_FOR_NATIVE:
        return False
    return has_images or len(full_text) < MIN_TEXT_CHARS_FOR_NATIVE


def render_region_png(page: fitz.Page, clip: fitz.Rect, *, zoom: float = 3.0) -> bytes:
    """Рендерит зону страницы в PNG (для схемы чертежа или OCR)."""
    matrix = fitz.Matrix(zoom, zoom)
    pixmap = page.get_pixmap(matrix=matrix, clip=clip, alpha=False)
    return pixmap.tobytes("png")
