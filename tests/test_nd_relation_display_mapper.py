from __future__ import annotations

import uuid

from app.models.enums import (
    ConfidenceLevel,
    NdGraphEntityType,
    NdRelationExtractionType,
    NdRelationType,
)
from app.models.nd_control_structural import DocumentCard, NdRelation, ProcessCard
from app.services.nd_relation_display_mapper import (
    RelationResolutionCache,
    evidence_has_content,
    is_uuid_like,
    map_relation_to_display,
    relation_display_flags,
)


def _relation(**overrides):
    base = dict(
        id=uuid.uuid4(),
        source_type=NdGraphEntityType.DOCUMENT,
        source_id=uuid.uuid4(),
        source_name="908f7140-cb95-47f5-be67-e2a48f17baf7",
        relation_type=NdRelationType.DOCUMENT_REGULATES_PROCESS,
        target_type=NdGraphEntityType.PROCESS,
        target_id=uuid.uuid4(),
        target_name="908f7140-cb95-47f5-be67-e2a48f17baf7",
        confidence=ConfidenceLevel.MEDIUM,
        extraction_type=NdRelationExtractionType.INFERRED,
        evidence_json=[{"document_code": "И-15-230", "quote": "ответственный за выполнение"}],
        is_confirmed=False,
    )
    base.update(overrides)
    return NdRelation(**base)


def test_confidence_medium_label_russian() -> None:
    relation = _relation(confidence=ConfidenceLevel.MEDIUM)
    cache = RelationResolutionCache()
    display = map_relation_to_display(relation, cache)
    assert display["confidence_label"] == "Средняя"


def test_extraction_type_inferred_label_russian() -> None:
    relation = _relation(extraction_type=NdRelationExtractionType.INFERRED)
    cache = RelationResolutionCache()
    display = map_relation_to_display(relation, cache)
    assert display["extraction_type_label"] == "Вывод агента"


def test_relation_type_label_russian() -> None:
    relation = _relation(relation_type=NdRelationType.DEPARTMENT_OWNS_PROCESS)
    cache = RelationResolutionCache()
    display = map_relation_to_display(relation, cache)
    assert display["relation_type_label"] == "Отдел владеет процессом"


def test_uuid_resolved_to_document_name() -> None:
    doc_id = uuid.uuid4()
    process_id = uuid.uuid4()
    relation = _relation(
        source_id=doc_id,
        source_name=str(doc_id),
        target_id=process_id,
        target_name=str(process_id),
    )
    cache = RelationResolutionCache()
    cache.documents_by_id[doc_id] = DocumentCard(
        document_id=doc_id,
        document_code="И-15-230",
        title="Резервное копирование",
    )
    cache.processes_by_id[process_id] = ProcessCard(
        id=process_id,
        canonical_name="Резервное копирование информационных ресурсов",
    )
    display = map_relation_to_display(relation, cache)
    assert display["source_display_name"] == "И-15-230 — Резервное копирование"
    assert display["target_display_name"] == "Резервное копирование информационных ресурсов"
    assert not is_uuid_like(display["source_display_name"])


def test_document_mentions_department_is_weak() -> None:
    relation = _relation(
        relation_type=NdRelationType.DOCUMENT_MENTIONS_DEPARTMENT,
        evidence_json=[],
    )
    flags = relation_display_flags(relation)
    assert flags["is_weak_relation"] is True


def test_department_owns_without_evidence_requires_review() -> None:
    relation = _relation(
        relation_type=NdRelationType.DEPARTMENT_OWNS_PROCESS,
        evidence_json=[],
    )
    flags = relation_display_flags(relation)
    assert flags["has_evidence"] is False
    assert flags["requires_review"] is True
    assert flags["can_bulk_approve"] is False


def test_display_contains_relation_description() -> None:
    relation = _relation()
    cache = RelationResolutionCache()
    display = map_relation_to_display(relation, cache)
    assert display["relation_description"]
    assert "document_code" not in display["relation_description"]


def test_explicit_high_with_evidence_can_bulk_approve() -> None:
    relation = _relation(
        extraction_type=NdRelationExtractionType.EXPLICIT,
        confidence=ConfidenceLevel.HIGH,
        evidence_json=[{"document_code": "И-15-230", "quote": "явно указано"}],
    )
    flags = relation_display_flags(relation)
    assert flags["can_bulk_approve"] is True


def test_evidence_has_content_ignores_department_profile_marker() -> None:
    assert evidence_has_content([{"source": "department_profile_build"}]) is False
