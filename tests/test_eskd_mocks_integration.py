from __future__ import annotations

import unittest
from unittest.mock import patch

from app.eskd.validation.engine import EskdValidationEngine
from app.eskd.validation.rules import check_designation_gost201_format
from app.services.eskd_validation_service import EskdValidationService
from tests.eskd import mocks


class TestEskdMockBuilders(unittest.TestCase):
    def test_valid_drawing_context_passes_engine(self) -> None:
        report = EskdValidationEngine().validate(mocks.build_valid_drawing_context())
        self.assertTrue(report.passed)
        self.assertGreaterEqual(report.score, 0.8)

    def test_invalid_designation_context_fails_gost201(self) -> None:
        context = mocks.build_invalid_designation_context()
        result = check_designation_gost201_format(context)
        self.assertFalse(result.passed)
        self.assertEqual(result.code, "designation_gost201")

    def test_missing_designation_context_fails_engine(self) -> None:
        report = EskdValidationEngine().validate(mocks.build_missing_designation_context())
        self.assertFalse(report.passed)
        failed_codes = {item.code for item in report.checks if not item.passed}
        self.assertIn("designation_present", failed_codes)

    def test_specification_context_has_markers(self) -> None:
        report = EskdValidationEngine().validate(mocks.build_specification_context())
        marker_check = next(item for item in report.checks if item.code == "specification_markers")
        self.assertTrue(marker_check.passed)

    def test_assembly_drawing_context_passes_kind_check(self) -> None:
        report = EskdValidationEngine().validate(mocks.build_assembly_drawing_context())
        kind_check = next(item for item in report.checks if item.code == "document_kind_consistency")
        self.assertTrue(kind_check.passed)

    def test_context_from_entities_matches_manual_builder(self) -> None:
        registration, document, _version, card = mocks.make_eskd_bundle()
        self.assertIsNotNone(card)
        from_entities = mocks.build_context_from_entities(registration, document, card=card)
        manual = mocks.build_valid_drawing_context()
        self.assertEqual(from_entities.designation, manual.designation)
        self.assertEqual(from_entities.document_kind, registration.document_kind)
        assert card is not None
        self.assertEqual(from_entities.qms_document_code, card.document_code)

    def test_minio_extracted_text_payload_roundtrip(self) -> None:
        text = mocks.drawing_inscription_text()
        payload = mocks.minio_extracted_text_payload(text)
        raw = mocks.minio_extracted_text_bytes(text)
        self.assertEqual(payload["text"], text)
        self.assertIn(b'"text"', raw)


class TestEskdValidationServiceMocks(unittest.IsolatedAsyncioTestCase):
    async def test_validate_registration_uses_minio_text(self) -> None:
        text = mocks.drawing_inscription_text()
        registration, document, version, card = mocks.make_eskd_bundle(document_text=text)
        self.assertIsNotNone(card)
        db = mocks.mock_validation_service_db(
            registration=registration,
            document=document,
            version=version,
            card=card,
        )
        service = EskdValidationService(db)

        with patch(
            "app.services.eskd_validation_service.object_storage.get_object",
            return_value=mocks.minio_extracted_text_bytes(text),
        ):
            report = await service.validate_registration(registration.id)

        self.assertTrue(report.passed)
        self.assertEqual(report.document_id, str(document.id))
        self.assertEqual(registration.status.value, "validated")
        self.assertEqual(db.flush.await_count, 1)

    async def test_validate_registration_falls_back_to_chunks(self) -> None:
        registration, document, version, card = mocks.make_eskd_bundle(with_card=False)
        chunk_text = mocks.drawing_inscription_text(title="Корпус из чанков")
        chunk = mocks.make_document_chunk(version, text=chunk_text)
        document.extracted_text_object_name = None
        version.extracted_text_object_name = None

        db = mocks.mock_validation_service_db(
            registration=registration,
            document=document,
            version=version,
            card=card,
            chunks=[chunk],
        )
        service = EskdValidationService(db)
        report = await service.validate_registration(registration.id)

        designation_check = next(item for item in report.checks if item.code == "designation_in_content")
        self.assertTrue(designation_check.passed)


if __name__ == "__main__":
    unittest.main()
