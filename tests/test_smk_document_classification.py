from __future__ import annotations

import uuid

import pytest

from app.models.enums import NdDocumentLevel, NdStructuralDocumentType
from app.models.nd_control_structural import DocumentCard
from app.utils.smk_document_classification import (
    DocumentLevel,
    DocumentType,
    get_document_level,
    get_document_level_label,
    get_document_type_label,
    sync_document_card_level,
)


@pytest.mark.parametrize(
    ("document_type", "expected_level"),
    [
        (NdStructuralDocumentType.POLICY, NdDocumentLevel.STRATEGIC),
        (NdStructuralDocumentType.REGULATION, NdDocumentLevel.ORGANIZATIONAL),
        (NdStructuralDocumentType.PROCESS_REGULATION, NdDocumentLevel.PROCESS),
        (NdStructuralDocumentType.STO, NdDocumentLevel.TECHNICAL),
        (NdStructuralDocumentType.INSTRUCTION, NdDocumentLevel.OPERATIONAL),
    ],
)
def test_get_document_level_mapping(document_type, expected_level) -> None:
    assert get_document_level(document_type) == expected_level
    assert DocumentLevel(expected_level.value) == expected_level


def test_get_document_level_returns_none_for_missing_type() -> None:
    assert get_document_level(None) is None


def test_sync_document_card_level_recalculates_on_type_change() -> None:
    card = DocumentCard(
        document_id=uuid.uuid4(),
        knowledge_base_id=uuid.uuid4(),
        document_type=NdStructuralDocumentType.INSTRUCTION,
        document_level=NdDocumentLevel.STRATEGIC.value,
    )
    card.document_type = NdStructuralDocumentType.REGULATION
    sync_document_card_level(card)
    assert card.document_level == NdDocumentLevel.ORGANIZATIONAL.value


@pytest.mark.parametrize(
    ("document_type", "type_label", "level_label"),
    [
        (NdStructuralDocumentType.POLICY, "Политика", "Стратегический"),
        (NdStructuralDocumentType.REGULATION, "Положение", "Организационный"),
        (NdStructuralDocumentType.PROCESS_REGULATION, "Регламент", "Процессный"),
        (NdStructuralDocumentType.STO, "СТО", "Технический"),
        (NdStructuralDocumentType.INSTRUCTION, "Инструкция", "Операционный"),
    ],
)
def test_russian_labels(document_type, type_label, level_label) -> None:
    assert get_document_type_label(document_type) == type_label
    assert get_document_type_label(DocumentType(document_type.value)) == type_label
    level = get_document_level(document_type)
    assert get_document_level_label(level) == level_label
