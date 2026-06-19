#!/usr/bin/env python3
"""Convert a single PDF page to a text-selectable PDF (OCR if needed)."""

from __future__ import annotations

import argparse
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse

import fitz

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "tests" / "eskd" / "fixtures" / "pdfs"


@dataclass
class PageAnalysis:
    page_index: int
    width: float
    height: float
    text_chars: int
    text_sample: str
    text_blocks: int
    image_blocks: int
    image_xrefs: int
    drawing_count: int
    has_meaningful_text: bool


def _resolve_source_path(raw: str) -> Path:
    raw = raw.strip()
    if raw.startswith("file://"):
        parsed = urlparse(raw)
        path = unquote(parsed.path)
        if parsed.netloc and not (len(path) > 2 and path[2] == ":"):
            windows_path = path.replace("/", "\\")
            return Path(f"\\\\{parsed.netloc}{windows_path}")
        if path.startswith("/") and len(path) > 2 and path[2] == ":":
            path = path[1:]
        return Path(path.replace("/", "\\"))
    return Path(raw)


def _find_cyrillic_font() -> str | None:
    candidates = [
        Path(r"C:\Windows\Fonts\arial.ttf"),
        Path(r"C:\Windows\Fonts\times.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
    ]
    for path in candidates:
        if path.is_file():
            return str(path)
    return None


def analyze_page(page: fitz.Page, page_index: int) -> PageAnalysis:
    text = page.get_text("text").strip()
    blocks = page.get_text("dict")["blocks"]
    text_blocks = sum(1 for block in blocks if block.get("type") == 0)
    image_blocks = sum(1 for block in blocks if block.get("type") == 1)
    return PageAnalysis(
        page_index=page_index,
        width=page.rect.width,
        height=page.rect.height,
        text_chars=len(text),
        text_sample=text[:400],
        text_blocks=text_blocks,
        image_blocks=image_blocks,
        image_xrefs=len(page.get_images(full=True)),
        drawing_count=len(page.get_drawings()),
        has_meaningful_text=len(text) >= 50,
    )


def _prepare_ocr_image(pix: fitz.Pixmap, clockwise_deg: int):
    from PIL import Image, ImageEnhance, ImageOps

    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    gray = ImageEnhance.Contrast(ImageOps.autocontrast(img.convert("L"))).enhance(1.35)
    ocr_img = gray.convert("RGB")
    if clockwise_deg:
        ocr_img = ocr_img.rotate(-clockwise_deg, expand=True, fillcolor=255)
    return ocr_img, pix.width, pix.height


def _map_rotated_rect_to_page(
    rect: fitz.Rect,
    *,
    pre_rot_width: int,
    clockwise_deg: int,
    scale: float,
) -> fitz.Rect:
    if clockwise_deg == 0:
        return _scale_rect(rect, scale)

    corners = [
        (rect.x0, rect.y0),
        (rect.x1, rect.y0),
        (rect.x0, rect.y1),
        (rect.x1, rect.y1),
    ]
    mapped: list[tuple[float, float]] = []
    for x_rot, y_rot in corners:
        if clockwise_deg == 90:
            mapped.append((pre_rot_width - y_rot, x_rot))
        elif clockwise_deg == 270:
            mapped.append((y_rot, pre_rot_width - x_rot))
        elif clockwise_deg == 180:
            mapped.append((pre_rot_width - x_rot, rect.y1 + rect.y0 - y_rot))
        else:
            raise ValueError(f"Unsupported OCR rotation: {clockwise_deg}")

    xs = [point[0] for point in mapped]
    ys = [point[1] for point in mapped]
    return fitz.Rect(min(xs) / scale, min(ys) / scale, max(xs) / scale, max(ys) / scale)


_EASYOCR_READER = None


def _get_easyocr_reader():
    global _EASYOCR_READER
    if _EASYOCR_READER is None:
        import easyocr

        _EASYOCR_READER = easyocr.Reader(["ru", "en"], gpu=False, verbose=False)
    return _EASYOCR_READER


def _ocr_with_tesseract(image, lang: str = "rus+eng") -> list[tuple[str, fitz.Rect]]:
    import pytesseract

    data = pytesseract.image_to_data(image, lang=lang, output_type=pytesseract.Output.DICT)
    items: list[tuple[str, fitz.Rect]] = []
    for i, word in enumerate(data["text"]):
        text = (word or "").strip()
        if not text:
            continue
        conf = int(data["conf"][i])
        if conf < 0:
            continue
        x = data["left"][i]
        y = data["top"][i]
        w = data["width"][i]
        h = data["height"][i]
        items.append((text, fitz.Rect(x, y, x + w, y + h)))
    return items


def _ocr_with_easyocr(image) -> list[tuple[str, fitz.Rect]]:
    import numpy as np

    reader = _get_easyocr_reader()
    arr = np.array(image)
    results = reader.readtext(arr, paragraph=False)
    items: list[tuple[str, fitz.Rect]] = []
    for bbox, text, _conf in results:
        text = (text or "").strip()
        if not text:
            continue
        xs = [point[0] for point in bbox]
        ys = [point[1] for point in bbox]
        rect = fitz.Rect(min(xs), min(ys), max(xs), max(ys))
        items.append((text, rect))
    return items


def _pick_ocr_method() -> str:
    if shutil.which("tesseract"):
        try:
            import pytesseract  # noqa: F401
            from PIL import Image  # noqa: F401
        except ImportError:
            pass
        else:
            return "pytesseract"
    try:
        import easyocr  # noqa: F401
    except ImportError:
        pass
    else:
        return "easyocr"
    raise RuntimeError(
        "OCR unavailable: install tesseract + pytesseract + pillow, or easyocr"
    )


def _ocr_page(image, method: str) -> list[tuple[str, fitz.Rect]]:
    if method == "pytesseract":
        return _ocr_with_tesseract(image)
    if method == "easyocr":
        return _ocr_with_easyocr(image)
    raise ValueError(f"Unknown OCR method: {method}")


def _scale_rect(rect: fitz.Rect, scale: float) -> fitz.Rect:
    return fitz.Rect(rect.x0 / scale, rect.y0 / scale, rect.x1 / scale, rect.y1 / scale)


def _insert_ocr_text(
    page: fitz.Page,
    ocr_items: list[tuple[str, fitz.Rect]],
    *,
    scale: float,
    fontfile: str | None,
    pre_rot_width: int,
    clockwise_deg: int,
) -> None:
    for text, rect in ocr_items:
        box = _map_rotated_rect_to_page(
            rect,
            pre_rot_width=pre_rot_width,
            clockwise_deg=clockwise_deg,
            scale=scale,
        )
        height = max(box.height, 4.0)
        fontsize = max(4.0, min(height * 0.85, 24.0))
        point = fitz.Point(box.x0, box.y1 - height * 0.15)
        if fontfile:
            page.insert_text(
                point,
                text,
                fontname="ocrfont",
                fontfile=fontfile,
                fontsize=fontsize,
                color=(0, 0, 0),
                render_mode=3,
            )
        else:
            page.insert_text(
                point,
                text,
                fontsize=fontsize,
                color=(0, 0, 0),
                render_mode=3,
            )


def _guess_ocr_rotation(page: fitz.Page) -> int:
    rotation = page.rotation % 360
    if rotation in {90, 180, 270}:
        return (360 - rotation) % 360
    return 90


def build_text_pdf_from_page(
    source_page: fitz.Page,
    *,
    dpi: int = 250,
    keep_background: bool = True,
    ocr_rotation: int | None = None,
) -> tuple[fitz.Document, str, list[tuple[str, fitz.Rect]], int]:
    analysis = analyze_page(source_page, source_page.number)
    if analysis.has_meaningful_text:
        out = fitz.open()
        out.insert_pdf(source_page.parent, from_page=source_page.number, to_page=source_page.number)
        return out, "existing_text_layer", [], 0

    clockwise_deg = ocr_rotation if ocr_rotation is not None else _guess_ocr_rotation(source_page)
    method = _pick_ocr_method()
    scale = dpi / 72.0
    matrix = fitz.Matrix(scale, scale)
    pix = source_page.get_pixmap(matrix=matrix, alpha=False)
    ocr_image, pre_rot_width, _pre_rot_height = _prepare_ocr_image(pix, clockwise_deg)
    ocr_items = _ocr_page(ocr_image, method)

    out = fitz.open()
    page = out.new_page(width=source_page.rect.width, height=source_page.rect.height)
    if keep_background:
        bg = source_page.get_pixmap(matrix=matrix, alpha=False)
        page.insert_image(page.rect, pixmap=bg)

    fontfile = _find_cyrillic_font()
    _insert_ocr_text(
        page,
        ocr_items,
        scale=scale,
        fontfile=fontfile,
        pre_rot_width=pre_rot_width,
        clockwise_deg=clockwise_deg,
    )
    return out, method, ocr_items, clockwise_deg


def convert_page(
    source: Path,
    output: Path,
    *,
    page_number: int,
    dpi: int = 250,
    keep_background: bool = True,
    ocr_rotation: int | None = None,
) -> dict[str, object]:
    if not source.is_file():
        raise FileNotFoundError(f"Source PDF not found: {source}")

    src = fitz.open(source)
    try:
        if page_number < 1 or page_number > src.page_count:
            raise ValueError(f"Page {page_number} out of range (1..{src.page_count})")
        page_index = page_number - 1
        source_page = src[page_index]
        analysis = analyze_page(source_page, page_index)

        out_doc, ocr_method, ocr_items, clockwise_deg = build_text_pdf_from_page(
            source_page,
            dpi=dpi,
            keep_background=keep_background,
            ocr_rotation=ocr_rotation,
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        out_doc.save(output, deflate=True)
        out_doc.close()

        verify = fitz.open(output)
        extracted = verify[0].get_text("text").strip()
        verify.close()

        return {
            "source": str(source),
            "output": str(output),
            "page_number": page_number,
            "analysis": analysis,
            "ocr_method": ocr_method,
            "ocr_rotation": clockwise_deg,
            "ocr_items_count": len(ocr_items),
            "output_text_chars": len(extracted),
            "output_text_sample": extracted[:500],
        }
    finally:
        src.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", help="Path or file:// URL to source PDF")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_DIR / "converted_page_text.pdf",
    )
    parser.add_argument("-p", "--page", type=int, default=9, help="1-based page number")
    parser.add_argument("--dpi", type=int, default=300)
    parser.add_argument("--ocr-rotation", type=int, default=None, help="Clockwise degrees to straighten scan")
    parser.add_argument("--no-background", action="store_true")
    args = parser.parse_args()

    source = _resolve_source_path(args.source)
    result = convert_page(
        source,
        args.output,
        page_number=args.page,
        dpi=args.dpi,
        keep_background=not args.no_background,
        ocr_rotation=args.ocr_rotation,
    )
    analysis: PageAnalysis = result["analysis"]  # type: ignore[assignment]

    print("=== Source page analysis ===")
    print(f"Source: {result['source']}")
    print(f"Page: {result['page_number']} (index {analysis.page_index})")
    print(f"Size: {analysis.width:.1f} x {analysis.height:.1f} pt")
    print(f"Text blocks: {analysis.text_blocks}, image blocks: {analysis.image_blocks}")
    print(f"Embedded images: {analysis.image_xrefs}, drawings: {analysis.drawing_count}")
    print(f"Extractable text chars (source): {analysis.text_chars}")
    if analysis.text_sample:
        print(f"Source text sample: {analysis.text_sample[:200]!r}")

    print("\n=== Conversion ===")
    print(f"OCR method: {result['ocr_method']}")
    print(f"OCR rotation (clockwise): {result['ocr_rotation']}°")
    print(f"OCR items: {result['ocr_items_count']}")
    print(f"Output: {result['output']}")

    print("\n=== Verification ===")
    print(f"Output text chars: {result['output_text_chars']}")
    print(f"Output text sample: {result['output_text_sample']!r}")
    ok = int(result["output_text_chars"]) >= 50
    print(f"PASS (>50 chars): {ok}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
