from __future__ import annotations

import uuid

from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.models.enums import (
    ConfidenceLevel,
    NdBuildStatus,
    NdExtractionStatus,
    NdGraphEntityType,
    NdRelationExtractionType,
    NdRelationType,
)
from app.schemas.nd_control_structural import (
    DepartmentProfileCreate,
    DocumentCardCreate,
    NdRelationCreate,
    NdRelationEvidenceItem,
    ProcessCardCreate,
)


def test_document_card_create_validates_confidence_range() -> None:
    card = DocumentCardCreate(
        document_id=uuid.uuid4(),
        knowledge_base_id=uuid.uuid4(),
        extraction_confidence="0.85",
        extraction_status=NdExtractionStatus.PENDING,
    )
    assert card.extraction_confidence == Decimal("0.85")

    with pytest.raises(ValidationError):
        DocumentCardCreate(
            document_id=uuid.uuid4(),
            knowledge_base_id=uuid.uuid4(),
            extraction_confidence="1.5",
        )


def test_department_profile_create_validates_functions() -> None:
    profile = DepartmentProfileCreate(
        department_id=uuid.uuid4(),
        department_name="Отдел качества",
        functions_json=[{"name": "Контроль НД", "description": "Ведение реестра"}],
        source_knowledge_base_ids=[str(uuid.uuid4())],
        build_status=NdBuildStatus.PENDING,
    )
    assert profile.functions_json is not None
    assert len(profile.source_knowledge_base_ids) == 1


def test_process_card_create_validates_nested_json() -> None:
    card = ProcessCardCreate(
        canonical_name="Согласование изменений НД",
        actions_json=[{"name": "Подготовка проекта", "order": 1}],
        roles_json=[{"name": "Ответственный за НД", "responsibilities": ["Проверка"]}],
        forms_json=[{"name": "Лист согласования", "code": "Ф-01"}],
        systems_json=[{"name": "1С", "kind": "erp"}],
        resources_json=[{"name": "NAS", "kind": "storage"}],
        owner_confidence=ConfidenceLevel.HIGH,
    )
    assert card.canonical_name.startswith("Согласование")


def test_nd_relation_create_validates_evidence() -> None:
    relation = NdRelationCreate(
        source_type=NdGraphEntityType.DOCUMENT,
        source_id=uuid.uuid4(),
        source_name="И-15-230",
        relation_type=NdRelationType.DOCUMENT_REGULATES_PROCESS,
        target_type=NdGraphEntityType.PROCESS,
        target_name="Входной контроль",
        extraction_type=NdRelationExtractionType.EXPLICIT,
        evidence_json=[
            NdRelationEvidenceItem(
                document_code="И-15-230",
                page=4,
                section="3.1",
                quote="Процесс входного контроля",
            )
        ],
    )
    assert relation.evidence_json[0].page == 4
