from __future__ import annotations

import uuid

import pytest

from app.agents.nd_control_agent.prompts.nd_process_uml_prompt import build_process_uml_user_prompt
from app.models.enums import NdStructuralDocumentType
from app.models.nd_control_structural import DocumentCard
from app.schemas.nd_process_graph import ProcessGraphDTO
from app.schemas.process_smk_sections import DiagramDetailLevel
from app.services.nd_process_uml_service import compute_content_version
from app.services.process_graph_builder import attach_source_document_context
from app.services.process_uml_document_profile import (
    DIAGRAM_PROFILE_LABELS,
    document_type_prompt_hint,
    select_primary_document_type,
)


def test_select_primary_document_type_prefers_sto() -> None:
    primary = select_primary_document_type(
        [
            NdStructuralDocumentType.POLICY,
            NdStructuralDocumentType.STO,
            NdStructuralDocumentType.INSTRUCTION,
        ]
    )
    assert primary == NdStructuralDocumentType.STO


def test_document_type_prompt_hint_for_sto() -> None:
    hint = document_type_prompt_hint(NdStructuralDocumentType.STO)
    assert "СТО" in hint
    assert "контроль" in hint.lower() or "критер" in hint.lower()


def test_document_type_prompt_hint_for_instruction() -> None:
    hint = document_type_prompt_hint(NdStructuralDocumentType.INSTRUCTION)
    assert "Инструкция" in hint
    assert "исполнител" in hint.lower() or "операцион" in hint.lower()


def test_document_type_prompt_hint_for_regulation() -> None:
    hint = document_type_prompt_hint(NdStructuralDocumentType.PROCESS_REGULATION)
    assert "Регламент" in hint
    assert "последователь" in hint.lower() or "алгоритм" in hint.lower()


def test_document_type_prompt_hint_for_policy_without_operations() -> None:
    hint = document_type_prompt_hint(NdStructuralDocumentType.POLICY)
    assert "Политика" in hint
    assert "не придумывай операции" in hint.lower()


@pytest.mark.parametrize(
    ("document_type", "expected_profile"),
    [
        (NdStructuralDocumentType.POLICY, "Схема обязательств и ответственности"),
        (NdStructuralDocumentType.REGULATION, "Схема функций и ответственности"),
        (NdStructuralDocumentType.PROCESS_REGULATION, "Алгоритм взаимодействия"),
        (NdStructuralDocumentType.STO, "Процессная схема с требованиями и контролем"),
        (NdStructuralDocumentType.INSTRUCTION, "Операционная схема выполнения"),
    ],
)
def test_diagram_profile_labels(document_type: NdStructuralDocumentType, expected_profile: str) -> None:
    assert DIAGRAM_PROFILE_LABELS[document_type] == expected_profile


def test_build_process_uml_user_prompt_includes_document_type_rules() -> None:
    context = {
        "process_graph": {
            "process_name": "Тест",
            "source_document_type": "STO",
            "source_document_type_label": "СТО",
            "qms_level": "TECHNICAL",
            "qms_level_label": "Технический",
            "actions": [],
            "roles": [],
        }
    }
    prompt = build_process_uml_user_prompt(context, detail_level=DiagramDetailLevel.DETAILED)
    assert "source_document_type" in prompt
    assert "СТО" in prompt
    assert "контроль" in prompt.lower() or "критер" in prompt.lower()


def test_attach_source_document_context_sets_qms_fields() -> None:
    graph = ProcessGraphDTO(process_id=str(uuid.uuid4()), process_name="Процесс")
    card = DocumentCard(
        document_id=uuid.uuid4(),
        knowledge_base_id=uuid.uuid4(),
        document_code="СТО-34-003",
        title="Стандарт",
        document_type=NdStructuralDocumentType.STO,
        document_level="TECHNICAL",
    )

    enriched = attach_source_document_context(graph, [card])

    assert enriched.source_document_type == "STO"
    assert enriched.source_document_type_label == "СТО"
    assert enriched.qms_level == "technical"
    assert enriched.qms_level_label == "Технический"
    assert enriched.diagram_profile_label == "Процессная схема с требованиями и контролем"
    assert enriched.primary_document_type == "STO"
    assert len(enriched.source_document_types) == 1


def test_compute_content_version_changes_when_source_document_type_changes() -> None:
    graph_a = ProcessGraphDTO(
        process_id=str(uuid.uuid4()),
        process_name="A",
        source_document_type="STO",
    )
    graph_b = ProcessGraphDTO(
        process_id=str(uuid.uuid4()),
        process_name="A",
        source_document_type="INSTRUCTION",
    )
    version_a = compute_content_version(graph_a, detail_level=DiagramDetailLevel.STANDARD)
    version_b = compute_content_version(graph_b, detail_level=DiagramDetailLevel.STANDARD)
    assert version_a != version_b
