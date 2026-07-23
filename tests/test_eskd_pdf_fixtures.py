from __future__ import annotations

import unittest
from pathlib import Path

from app.eskd.validation.engine import EskdValidationEngine
from app.models.enums import DocumentType, EskdDocumentKind, TextExtractStatus
from tests.eskd.mocks import DEFAULT_DESIGNATION, DEFAULT_TITLE, build_validation_context

PDF_DIR = Path(__file__).resolve().parent / "eskd" / "fixtures" / "pdfs"

VALID_PDF = PDF_DIR / "ABVG.123456.001.pdf"
SCAN_PDF = PDF_DIR / "ABVG.123456.001_scan.pdf"
WRONG_PDF = PDF_DIR / "WRONG_FILENAME.pdf"


def _require_fitz():
    try:
        import fitz  # noqa: PLC0415

        return fitz
    except ImportError as exc:
        raise unittest.SkipTest("pymupdf (fitz) is not installed") from exc


def _extract_pdf_text(path: Path) -> str:
    fitz = _require_fitz()
    if not path.is_file():
        raise unittest.SkipTest(f"PDF fixture missing: {path}")
    with fitz.open(path) as doc:
        return "".join(page.get_text() for page in doc)


def _check_map(report):
    return {item.code: item for item in report.checks}


class TestEskdPdfFixtures(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.engine = EskdValidationEngine()
        cls.valid_text = _extract_pdf_text(VALID_PDF)
        cls.scan_text = _extract_pdf_text(SCAN_PDF)
        cls.wrong_text = _extract_pdf_text(WRONG_PDF)

    def test_valid_pdf_passes_validation(self) -> None:
        context = build_validation_context(
            designation=DEFAULT_DESIGNATION,
            document_title=DEFAULT_TITLE,
            original_filename=VALID_PDF.name,
            document_text=self.valid_text,
            text_extract_status=TextExtractStatus.EXTRACTED,
        )
        report = self.engine.validate(context)
        checks = _check_map(report)

        self.assertTrue(report.passed)
        self.assertGreaterEqual(report.score, 0.8)
        for code in (
            "designation_present",
            "designation_gost201",
            "designation_filename",
            "designation_in_content",
            "main_inscription",
            "drawing_scale",
        ):
            self.assertTrue(checks[code].passed, msg=code)

    def test_scan_pdf_before_ocr(self) -> None:
        self.assertEqual(self.scan_text.strip(), "")
        context = build_validation_context(
            designation=DEFAULT_DESIGNATION,
            document_title=DEFAULT_TITLE,
            original_filename=SCAN_PDF.name,
            document_text="",
            text_extract_status=TextExtractStatus.NOT_STARTED,
        )
        report = self.engine.validate(context)
        checks = _check_map(report)

        self.assertTrue(report.passed)
        self.assertGreaterEqual(report.score, 0.7)
        self.assertLess(report.score, 0.85)
        self.assertFalse(checks["text_extracted"].passed)
        self.assertFalse(checks["designation_in_content"].passed)
        self.assertFalse(checks["main_inscription"].passed)
        self.assertFalse(checks["drawing_scale"].passed)
        self.assertTrue(checks["designation_gost201"].passed)

    def test_scan_pdf_after_ocr(self) -> None:
        context = build_validation_context(
            designation=DEFAULT_DESIGNATION,
            document_title=DEFAULT_TITLE,
            original_filename=SCAN_PDF.name,
            document_text=self.valid_text,
            text_extract_status=TextExtractStatus.EXTRACTED,
        )
        report = self.engine.validate(context)
        checks = _check_map(report)

        self.assertTrue(report.passed)
        self.assertGreaterEqual(report.score, 0.8)
        self.assertTrue(checks["text_extracted"].passed)
        self.assertTrue(checks["designation_in_content"].passed)
        self.assertTrue(checks["main_inscription"].passed)
        self.assertTrue(checks["drawing_scale"].passed)

    def test_wrong_filename_pdf_fails_validation(self) -> None:
        context = build_validation_context(
            designation="INVALID DOC",
            document_title="Деталь без кода",
            original_filename=WRONG_PDF.name,
            document_kind=EskdDocumentKind.DRAWING,
            document_type=DocumentType.KD,
            document_text=self.wrong_text,
            text_extract_status=TextExtractStatus.EXTRACTED,
            qms_document_code="INVALID DOC",
        )
        report = self.engine.validate(context)
        checks = _check_map(report)

        self.assertFalse(report.passed)
        self.assertLess(report.score, 0.3)
        for code in (
            "designation_characters",
            "designation_gost201",
            "designation_filename",
            "main_inscription",
            "designation_in_content",
        ):
            self.assertFalse(checks[code].passed, msg=code)
        self.assertFalse(checks["drawing_scale"].passed)


if __name__ == "__main__":
    unittest.main()
