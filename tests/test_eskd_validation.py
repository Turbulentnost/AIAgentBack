from __future__ import annotations

import unittest

from app.eskd.designation import parse_designation
from app.eskd.validation.engine import EskdValidationEngine
from app.eskd.validation.rules import check_designation_gost201_format
from tests.eskd.mocks import (
    build_invalid_designation_context,
    build_missing_designation_context,
    build_valid_drawing_context,
)


class TestEskdValidation(unittest.TestCase):
    def test_parse_designation_standard(self) -> None:
        parsed = parse_designation("ABVG.123456.001")
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed["org"], "ABVG")
        self.assertEqual(parsed["serial"], "123456")
        self.assertEqual(parsed["sheet"], "001")

    def test_validation_engine_passes_minimal_kd_document(self) -> None:
        report = EskdValidationEngine().validate(build_valid_drawing_context())
        self.assertTrue(report.passed)
        self.assertGreaterEqual(report.score, 0.8)

    def test_validation_engine_fails_without_designation(self) -> None:
        report = EskdValidationEngine().validate(build_missing_designation_context())
        self.assertFalse(report.passed)
        failed_codes = {item.code for item in report.checks if not item.passed}
        self.assertIn("designation_present", failed_codes)

    def test_gost201_format_check(self) -> None:
        result = check_designation_gost201_format(build_invalid_designation_context())
        self.assertFalse(result.passed)


if __name__ == "__main__":
    unittest.main()
