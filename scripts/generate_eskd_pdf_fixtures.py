#!/usr/bin/env python3
"""Generate ESKD/KD PDF fixtures for validation and OCR pipeline testing."""

from __future__ import annotations

import os
import random
import sys
from functools import lru_cache
from pathlib import Path

import fitz

PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = PROJECT_ROOT / "tests" / "eskd" / "fixtures" / "pdfs"

# A4 landscape (points)
PAGE_W = 842
PAGE_H = 595

VALID_DESIGNATION = "ABVG.123456.001"
VALID_TITLE = "Корпус"
INVALID_INFORMAL_CODE = "INVALID DOC"


def _find_cyrillic_font() -> Path:
    candidates = [
        Path(r"C:\Windows\Fonts\arial.ttf"),
        Path(r"C:\Windows\Fonts\times.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"),
        Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
    ]
    for path in candidates:
        if path.is_file():
            return path
    raise FileNotFoundError(
        "Cyrillic TTF font not found. Install Arial/DejaVu Sans or set ESKD_FIXTURE_FONT."
    )


@lru_cache(maxsize=1)
def _font_path() -> str:
    env_font = os.environ.get("ESKD_FIXTURE_FONT")
    if env_font:
        path = Path(env_font)
        if path.is_file():
            return str(path)
    return str(_find_cyrillic_font())


def _insert_text(
    page: fitz.Page,
    point: tuple[float, float],
    text: str,
    *,
    fontsize: float = 9,
    color: tuple[float, float, float] = (0, 0, 0),
) -> None:
    page.insert_text(
        point,
        text,
        fontname="eskd",
        fontfile=_font_path(),
        fontsize=fontsize,
        color=color,
    )


def _draw_title_block(
    page: fitz.Page,
    origin: tuple[float, float],
    rows: list[tuple[str, str]],
    *,
    label_width: float = 72,
    row_height: float = 14,
    value_width: float = 130,
) -> None:
    x0, y0 = origin
    block_w = label_width + value_width
    block_h = row_height * len(rows)
    rect = fitz.Rect(x0, y0, x0 + block_w, y0 + block_h)
    page.draw_rect(rect, color=(0, 0, 0), width=0.8)

    for idx, (label, value) in enumerate(rows):
        y = y0 + idx * row_height
        page.draw_line((x0, y), (x0 + block_w, y), color=(0, 0, 0), width=0.5)
        page.draw_line((x0 + label_width, y), (x0 + label_width, y + row_height), color=(0, 0, 0), width=0.5)
        _insert_text(page, (x0 + 2, y + 10), label, fontsize=7)
        _insert_text(page, (x0 + label_width + 4, y + 10), value, fontsize=8)


def _draw_simple_part(page: fitz.Page) -> None:
    frame = fitz.Rect(40, 40, PAGE_W - 220, PAGE_H - 120)
    page.draw_rect(frame, color=(0, 0, 0), width=1.2)

    body = fitz.Rect(frame.x0 + 80, frame.y0 + 60, frame.x1 - 80, frame.y1 - 50)
    page.draw_rect(body, color=(0, 0, 0), width=1.0)

    cx = (body.x0 + body.x1) / 2
    cy = (body.y0 + body.y1) / 2
    page.draw_circle((cx, cy), 28, color=(0, 0, 0), width=0.8)

    hole_y = cy
    for hx in (cx - 45, cx + 45):
        page.draw_circle((hx, hole_y), 6, color=(0, 0, 0), width=0.8)
        page.draw_line((hx - 6, hole_y), (hx + 6, hole_y), color=(0, 0, 0), width=0.4)

    dim_y = body.y1 + 18
    page.draw_line((body.x0, dim_y), (body.x1, dim_y), color=(0, 0, 0), width=0.5)
    page.draw_line((body.x0, dim_y - 4), (body.x0, dim_y + 4), color=(0, 0, 0), width=0.5)
    page.draw_line((body.x1, dim_y - 4), (body.x1, dim_y + 4), color=(0, 0, 0), width=0.5)
    _insert_text(page, ((body.x0 + body.x1) / 2 - 20, dim_y + 12), "120", fontsize=8)


def create_valid_drawing_pdf(path: Path) -> None:
    doc = fitz.open()
    page = doc.new_page(width=PAGE_W, height=PAGE_H)

    _draw_simple_part(page)
    _insert_text(page, (50, 24), f"Чертёж детали {VALID_DESIGNATION}", fontsize=11)

    rows = [
        ("Обозначение", VALID_DESIGNATION),
        ("Наименование", VALID_TITLE),
        ("Масштаб", "1:2"),
        ("Лист", "1"),
        ("Листов", "2"),
        ("Разработал", "Иванов И.И."),
        ("Проверил", "Петров П.П."),
        ("Утвердил", "Сидоров С.С."),
    ]
    _draw_title_block(page, (PAGE_W - 215, PAGE_H - 125), rows)

    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(path, deflate=True)
    doc.close()


def create_invalid_drawing_pdf(path: Path) -> None:
    doc = fitz.open()
    page = doc.new_page(width=PAGE_W, height=PAGE_H)

    _draw_simple_part(page)
    _insert_text(page, (50, 24), "Чертёж (без корректного кода)", fontsize=11)
    _insert_text(page, (120, 180), INVALID_INFORMAL_CODE, fontsize=14, color=(0.6, 0, 0))

    # Deliberately wrong title block: no «Обозначение», no «Масштаб», informal code field.
    rows = [
        ("Код документа", INVALID_INFORMAL_CODE),
        ("Наименование", "Деталь без кода"),
        ("Лист", "1"),
        ("Листов", "1"),
        ("Разработал", "—"),
    ]
    _draw_title_block(page, (PAGE_W - 215, PAGE_H - 95), rows)

    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(path, deflate=True)
    doc.close()


def _add_noise(pix: fitz.Pixmap, *, strength: int = 14, seed: int = 42) -> fitz.Pixmap:
    rng = random.Random(seed)
    noisy = fitz.Pixmap(pix, 0)
    for y in range(0, noisy.height, 2):
        for x in range(0, noisy.width, 2):
            pixel = noisy.pixel(x, y)
            if len(pixel) < 3:
                continue
            delta = rng.randint(-strength, strength)
            noisy.set_pixel(
                x,
                y,
                tuple(max(0, min(255, channel + delta)) for channel in pixel[:3]),
            )
    return noisy


def create_scan_drawing_pdf(source_path: Path, path: Path) -> None:
    src = fitz.open(source_path)
    page = src[0]
    matrix = fitz.Matrix(2, 2).prerotate(0.7)
    pix = page.get_pixmap(matrix=matrix, alpha=False)
    pix = _add_noise(pix, strength=10, seed=7)
    src.close()

    scan_doc = fitz.open()
    scan_page = scan_doc.new_page(width=pix.width, height=pix.height)
    scan_page.insert_image(scan_page.rect, pixmap=pix)
    path.parent.mkdir(parents=True, exist_ok=True)
    scan_doc.save(path, deflate=True)
    scan_doc.close()
    pix = None


def verify_pdfs(paths: list[Path]) -> None:
    for pdf_path in paths:
        if not pdf_path.is_file():
            raise FileNotFoundError(f"Missing fixture: {pdf_path}")
        size = pdf_path.stat().st_size
        if size <= 0:
            raise ValueError(f"Empty fixture: {pdf_path}")
        doc = fitz.open(pdf_path)
        page_count = doc.page_count
        try:
            if page_count < 1:
                raise ValueError(f"No pages in {pdf_path}")
            _ = doc[0].rect
        finally:
            doc.close()
        print(f"OK  {pdf_path.name}: {size} bytes, {page_count} page(s)")


def main() -> int:
    valid_path = OUTPUT_DIR / f"{VALID_DESIGNATION}.pdf"
    scan_path = OUTPUT_DIR / f"{VALID_DESIGNATION}_scan.pdf"
    invalid_path = OUTPUT_DIR / "WRONG_FILENAME.pdf"

    print(f"Output directory: {OUTPUT_DIR}")
    create_valid_drawing_pdf(valid_path)
    create_scan_drawing_pdf(valid_path, scan_path)
    create_invalid_drawing_pdf(invalid_path)

    paths = [valid_path, scan_path, invalid_path]
    verify_pdfs(paths)

    valid_doc = fitz.open(valid_path)
    text = valid_doc[0].get_text().strip()
    valid_doc.close()
    print(f"Valid PDF text layer chars: {len(text)} (selectable text expected)")

    scan_doc = fitz.open(scan_path)
    scan_text = scan_doc[0].get_text().strip()
    scan_doc.close()
    print(f"Scan PDF text layer chars: {len(scan_text)} (image-only expected)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
