from __future__ import annotations

import uuid
from importlib import import_module

import pytest

from app.models.enums import NdTemplateClassificationStatus, NdTemplateType
from app.models.nd_control_templates import (
    NdControlTemplate,
    NdControlTemplateDocument,
    NdControlTemplateKnowledgeBase,
)
from app.utils.nd_template_classification import ND_TEMPLATE_TYPE_LABELS, get_template_type_label


def test_template_type_labels_cover_all_enum_values() -> None:
    assert len(NdTemplateType) == 15
    assert set(ND_TEMPLATE_TYPE_LABELS) == set(NdTemplateType)
    assert get_template_type_label(NdTemplateType.POLICY) == "Политика"
    assert get_template_type_label("process_passport") == "Паспорт процесса"
    assert get_template_type_label("unknown") is None


def test_template_models_define_expected_constraints() -> None:
    template_constraints = {constraint.name for constraint in NdControlTemplate.__table__.constraints}
    kb_constraints = {constraint.name for constraint in NdControlTemplateKnowledgeBase.__table__.constraints}
    document_constraints = {constraint.name for constraint in NdControlTemplateDocument.__table__.constraints}

    assert "uq_nd_control_templates_template_type" in template_constraints
    assert "uq_nd_control_template_knowledge_bases_template_kb" in kb_constraints
    assert "uq_nd_control_template_documents_template_source" in document_constraints


def test_template_document_defaults_to_pending_classification() -> None:
    document = NdControlTemplateDocument(
        template_id=uuid.uuid4(),
        knowledge_base_id=uuid.uuid4(),
        knowledge_base_source_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        document_version_id=uuid.uuid4(),
    )

    assert document.classification_status is None or document.classification_status == NdTemplateClassificationStatus.PENDING
    assert document.detected_template_type is None
    assert document.classification_confidence is None


@pytest.mark.asyncio
async def test_seed_nd_control_templates_iterates_all_types(monkeypatch) -> None:
    seed_module = import_module("scripts.seed_nd_control_templates")

    created: list[NdTemplateType] = []

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def commit(self):
            return None

    class FakeService:
        def __init__(self, db):
            self.db = db

        async def create_template(self, *, template_type, name, sort_order):
            assert name == ND_TEMPLATE_TYPE_LABELS[template_type]
            assert sort_order > 0
            created.append(template_type)

    monkeypatch.setattr(seed_module, "AsyncSessionLocal", lambda: FakeSession())
    monkeypatch.setattr(seed_module, "NdControlTemplateService", FakeService)

    count = await seed_module.seed_nd_control_templates()

    assert count == 15
    assert created == list(ND_TEMPLATE_TYPE_LABELS)
