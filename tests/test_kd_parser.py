from __future__ import annotations

import unittest
from pathlib import Path

import fitz

from app.services.document_processing.kd.formats import KDFormatError, detect_kd_format
from app.services.document_processing.kd.parser import KDParser
from app.services.document_processing.kd.regions import (
    TITLE_BLOCK_HEIGHT_RATIO,
    TITLE_BLOCK_WIDTH_RATIO,
    detect_page_regions,
    page_requires_ocr,
)

PDF_DIR = Path(__file__).resolve().parent / "eskd" / "fixtures" / "pdfs"
VALID_PDF = PDF_DIR / "ABVG.123456.001.pdf"
SCAN_PDF = PDF_DIR / "ABVG.123456.001_scan.pdf"


def _make_kd_pdf_with_stamp() -> bytes:
    """Минимальный PDF с основной надписью в штампе (без внешних фикстур)."""
    doc = fitz.open()
    page = doc.new_page(width=842, height=595)
    x0 = 842 * (1 - TITLE_BLOCK_WIDTH_RATIO)
    y0 = 595 * (1 - TITLE_BLOCK_HEIGHT_RATIO)
    font_kwargs: dict = {}
    for candidate in (
        Path(r"C:\Windows\Fonts\arial.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    ):
        if candidate.is_file():
            font_kwargs = {"fontname": "kdtest", "fontfile": str(candidate)}
            break
    page.insert_text((x0 + 8, y0 + 20), "Обозначение ABVG.123456.001", fontsize=10, **font_kwargs)
    page.insert_text((x0 + 8, y0 + 36), "Наименование Корпус", fontsize=10, **font_kwargs)
    page.insert_text((x0 + 8, y0 + 52), "Масштаб 1:2", fontsize=10, **font_kwargs)
    page.draw_rect(fitz.Rect(50, 50, x0 - 20, y0 - 20), color=(0, 0, 0), width=1)
    data = doc.tobytes()
    doc.close()
    return data


class TestKDFormatDetection(unittest.TestCase):
    def test_detect_pdf_magic(self) -> None:
        self.assertEqual(detect_kd_format(b"%PDF-1.4\n"), "pdf")

    def test_detect_by_extension(self) -> None:
        self.assertEqual(detect_kd_format(b"", filename="drawing.dxf"), "dxf")
        self.assertEqual(detect_kd_format(b"", filename="drawing.dwg"), "dwg")

    def test_unknown_format_raises(self) -> None:
        with self.assertRaises(KDFormatError):
            detect_kd_format(b"hello", filename="readme.txt")


class TestKDRegions(unittest.TestCase):
    def test_title_block_in_bottom_right(self) -> None:
        data = _make_kd_pdf_with_stamp()
        with fitz.open(stream=data, filetype="pdf") as doc:
            regions = detect_page_regions(doc[0])
            self.assertGreater(regions.title_block.x0, doc[0].rect.width * 0.5)
            self.assertGreater(regions.title_block.y0, doc[0].rect.height * 0.5)
            self.assertLessEqual(regions.drawing.x1, regions.title_block.x0)

    def test_native_pdf_does_not_require_ocr(self) -> None:
        data = _make_kd_pdf_with_stamp()
        with fitz.open(stream=data, filetype="pdf") as doc:
            page = doc[0]
            regions = detect_page_regions(page)
            self.assertFalse(page_requires_ocr(page, title_block=regions.title_block))


class TestKDParser(unittest.TestCase):
    def test_parse_inline_pdf(self) -> None:
        data = _make_kd_pdf_with_stamp()
        result = KDParser().parse_bytes(data, filename="test.pdf", page_numbers=[1])
        self.assertEqual(result.source_format, "pdf")
        self.assertEqual(result.pages_count, 1)
        page = result.pages[0]
        self.assertFalse(page.requires_ocr)
        self.assertIn("ABVG.123456.001", page.title_block.text)
        self.assertIn("обозначение", page.eskd_text.lower())
        self.assertIsNotNone(page.scheme_png_base64)
        self.assertIn("ABVG.123456.001", result.eskd_document_text)

    def test_dxf_parsed_via_parser(self) -> None:
        import io

        import ezdxf

        doc = ezdxf.new("R2010")
        doc.modelspace().add_text("ABVG.999.001", dxfattribs={"height": 2.5, "insert": (700, 50)})
        stream = io.StringIO()
        doc.write(stream)
        result = KDParser().parse_bytes(stream.getvalue().encode("utf-8"), filename="x.dxf", render_scheme=False)
        self.assertEqual(result.source_format, "dxf")
        self.assertIn("ABVG.999.001", result.eskd_document_text)

    @unittest.skipUnless(VALID_PDF.is_file(), f"fixture missing: {VALID_PDF}")
    def test_parse_valid_fixture(self) -> None:
        result = KDParser().parse_path(VALID_PDF, page_numbers=[1])
        page = result.pages[0]
        self.assertFalse(page.requires_ocr)
        self.assertGreater(page.title_block.char_count, 10)

    @unittest.skipUnless(SCAN_PDF.is_file(), f"fixture missing: {SCAN_PDF}")
    def test_scan_fixture_requires_ocr(self) -> None:
        result = KDParser().parse_path(SCAN_PDF, page_numbers=[1])
        page = result.pages[0]
        self.assertTrue(page.requires_ocr)
        self.assertEqual(page.title_block.method, "pending_ocr")
        self.assertIsNotNone(page.scheme_png_base64)


if __name__ == "__main__":
    unittest.main()
