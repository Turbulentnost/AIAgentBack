from __future__ import annotations

import io
import unittest

import ezdxf

from app.services.document_processing.kd.dxf import parse_kd_dxf_bytes
from app.services.document_processing.kd.parser import KDParser
from app.services.document_processing.kd.regions import TITLE_BLOCK_HEIGHT_RATIO, TITLE_BLOCK_WIDTH_RATIO


def _make_kd_dxf_with_stamp(*, designation: str = "ABVG.123456.001") -> bytes:
    """Синтетический DXF с штампом в правом нижнем углу."""
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()

    width, height = 842.0, 595.0
    tb_x0 = width * (1 - TITLE_BLOCK_WIDTH_RATIO)
    tb_y0 = height * (1 - TITLE_BLOCK_HEIGHT_RATIO)

    msp.add_lwpolyline(
        [(50, 50), (tb_x0 - 20, 50), (tb_x0 - 20, tb_y0 - 20), (50, tb_y0 - 20), (50, 50)],
        close=True,
    )
    msp.add_text(
        f"Обозначение {designation}",
        dxfattribs={"height": 3.5, "insert": (tb_x0 + 8, tb_y0 + 20)},
    )
    msp.add_text(
        "Наименование Корпус",
        dxfattribs={"height": 3.5, "insert": (tb_x0 + 8, tb_y0 + 36)},
    )
    msp.add_text(
        "Масштаб 1:2",
        dxfattribs={"height": 3.5, "insert": (tb_x0 + 8, tb_y0 + 52)},
    )
    msp.add_text(
        "UFG-800-16.02.11.000 СБ",
        dxfattribs={"height": 2.5, "insert": (120, 400)},
    )

    stream = io.StringIO()
    doc.write(stream)
    return stream.getvalue().encode("utf-8")


class TestKDDxfParser(unittest.TestCase):
    def test_parse_inline_dxf(self) -> None:
        data = _make_kd_dxf_with_stamp()
        result = parse_kd_dxf_bytes(data, filename="test.dxf", render_scheme=False)
        self.assertEqual(result.source_format, "dxf")
        self.assertEqual(result.pages_count, 1)
        page = result.pages[0]
        self.assertFalse(page.requires_ocr)
        self.assertEqual(page.title_block.method, "dxf")
        self.assertIn("ABVG.123456.001", page.title_block.text)
        self.assertIn("обозначение", page.eskd_text.lower())
        self.assertIn("ABVG.123456.001", result.eskd_document_text)
        self.assertEqual(result.metadata.get("detected_designation"), "ABVG.123456.001")

    def test_detect_ufg_designation(self) -> None:
        data = _make_kd_dxf_with_stamp(designation="UFG-800-16.02.11.000 СБ")
        result = parse_kd_dxf_bytes(data, filename="drawing.dxf", render_scheme=False)
        self.assertIn("UFG-800", result.metadata.get("detected_designation", ""))

    def test_kd_parser_routes_dxf(self) -> None:
        data = _make_kd_dxf_with_stamp()
        result = KDParser().parse_bytes(data, filename="x.dxf", render_scheme=False)
        self.assertEqual(result.source_format, "dxf")
        self.assertGreater(page.title_block.char_count if (page := result.pages[0]) else 0, 5)

    def test_render_scheme_png(self) -> None:
        data = _make_kd_dxf_with_stamp()
        result = parse_kd_dxf_bytes(data, filename="test.dxf", render_scheme=True, scheme_zoom=1.5)
        page = result.pages[0]
        if page.scheme_png_base64:
            self.assertGreater(len(page.scheme_png_base64), 100)


if __name__ == "__main__":
    unittest.main()
