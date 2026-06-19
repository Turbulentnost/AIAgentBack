from __future__ import annotations

from app.models.enums import QmsDocumentKind, QmsLevel
from app.services.document_card_utils import (
    extract_document_code,
    infer_document_kind,
    infer_qms_level,
)


def test_extract_document_code_from_filename() -> None:
    code = extract_document_code(
        title="Порядок разработки",
        original_filename="СТО-34-003.docx",
        metadata=None,
    )
    assert code == "СТО-34-003"


def test_infer_kind_and_level_for_sto() -> None:
    kind = infer_document_kind("СТО-34-003")
    assert kind == QmsDocumentKind.STO
    assert infer_qms_level(kind) == QmsLevel.TECHNICAL


def test_infer_kind_and_level_for_regulation() -> None:
    kind = infer_document_kind("РГ-12-001")
    assert kind == QmsDocumentKind.REGULATION
    assert infer_qms_level(kind) == QmsLevel.PROCESS


def test_infer_kind_and_level_for_policy() -> None:
    kind = infer_document_kind("ПП-01-001")
    assert kind == QmsDocumentKind.POLICY
    assert infer_qms_level(kind) == QmsLevel.STRATEGIC


def test_infer_kind_and_level_for_instruction() -> None:
    kind = infer_document_kind("И-05-010")
    assert kind == QmsDocumentKind.INSTRUCTION
    assert infer_qms_level(kind) == QmsLevel.OPERATIONAL
