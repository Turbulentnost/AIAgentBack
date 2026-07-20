from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.enums import (
    ConfidenceLevel,
    NdGraphEntityType,
    NdRelationExtractionType,
    NdRelationType,
)
from app.models.nd_control_structural import NdRelation, ProcessCard
from app.schemas.diagram_block import DiagramBlockType
from app.services.diagram_block_classifier import classify_diagram_block
from app.services.process_graph_builder import (
    ProcessGraphBuilder,
    ProcessGraphBuilderError,
    assemble_process_graph,
    attach_source_document_context,
    extract_graph_fragments,
)
from app.models.enums import NdStructuralDocumentType
from app.models.nd_control_structural import DocumentCard


def _process(**kwargs) -> ProcessCard:
    defaults = {
        "id": uuid.uuid4(),
        "canonical_name": "Управление контрактом",
        "owner_confirmed": False,
        "goal": "Обеспечить управление контрактом",
        "owner_candidate": "Менеджер",
        "actions_json": [{"action": "Согласовать", "performer": "Менеджер"}],
        "roles_json": ["Юрист"],
        "inputs_json": ["Заявка клиента"],
        "outputs_json": ["Подписанный контракт"],
        "forms_json": ["Форма заявки"],
        "systems_json": ["1С"],
        "resources_json": ["Бланк договора"],
        "updated_at": datetime(2026, 6, 17, tzinfo=timezone.utc),
    }
    defaults.update(kwargs)
    return ProcessCard(**defaults)


def _relation(**kwargs) -> NdRelation:
    defaults = {
        "id": uuid.uuid4(),
        "source_type": NdGraphEntityType.PROCESS,
        "source_id": uuid.uuid4(),
        "source_name": "Процесс",
        "relation_type": NdRelationType.PROCESS_HAS_ROLE,
        "target_type": NdGraphEntityType.ROLE,
        "target_id": None,
        "target_name": "Менеджер",
        "confidence": ConfidenceLevel.MEDIUM,
        "extraction_type": NdRelationExtractionType.INFERRED,
        "evidence_json": None,
        "is_confirmed": False,
    }
    defaults.update(kwargs)
    return NdRelation(**defaults)


def test_extract_graph_fragments_from_relations() -> None:
    process_id = uuid.uuid4()
    relations = [
        _relation(
            source_id=process_id,
            relation_type=NdRelationType.PROCESS_HAS_ROLE,
            target_name="Юрист",
        ),
        _relation(
            source_id=process_id,
            relation_type=NdRelationType.PROCESS_CONSUMES_INPUT,
            target_type=NdGraphEntityType.RESOURCE,
            target_name="Заявка",
        ),
        _relation(
            source_id=process_id,
            relation_type=NdRelationType.PROCESS_PRODUCES_OUTPUT,
            target_type=NdGraphEntityType.RESOURCE,
            target_name="Контракт",
        ),
        _relation(
            source_id=process_id,
            relation_type=NdRelationType.PROCESS_USES_SYSTEM,
            target_type=NdGraphEntityType.SYSTEM,
            target_name="1С",
        ),
        _relation(
            source_id=process_id,
            relation_type=NdRelationType.PROCESS_USES_FORM,
            target_type=NdGraphEntityType.FORM,
            target_name="Форма заявки",
        ),
        _relation(
            source_type=NdGraphEntityType.DOCUMENT,
            source_name="СТО-34-003",
            target_id=process_id,
            target_type=NdGraphEntityType.PROCESS,
            relation_type=NdRelationType.DOCUMENT_REGULATES_PROCESS,
        ),
    ]

    fragments = extract_graph_fragments(relations, process_id=process_id)

    assert fragments["actors"] == ["Юрист"]
    assert fragments["inputs"] == ["Заявка"]
    assert fragments["outputs"] == ["Контракт"]
    assert fragments["systems"] == ["1С"]
    assert fragments["forms"] == ["Форма заявки"]
    assert fragments["documents"] == ["СТО-34-003"]


def test_classify_diagram_block_types() -> None:
    decision_type, _ = classify_diagram_block({"title": "Согласовано?"})
    assert decision_type == DiagramBlockType.DECISION

    document_type, _ = classify_diagram_block({"title": "Подготовить извещение об изменении"})
    assert document_type == DiagramBlockType.DOCUMENT_OUTPUT

    subprocess_type, _ = classify_diagram_block({"title": "Выполнить согласно инструкции"})
    assert subprocess_type == DiagramBlockType.SUBPROCESS

    start_type, _ = classify_diagram_block({"title": "Инициация процесса"})
    assert start_type == DiagramBlockType.START


def test_assemble_process_graph_with_block_types_and_smk_fields() -> None:
    process = _process(
        actions_json=[
            {"action": "Инициация заявки", "performer": "Менеджер"},
            {"action": "Согласовано?", "performer": "Юрист"},
            {"action": "Подготовить извещение", "performer": "Менеджер"},
            {"action": "Сдача в архив", "performer": "Архивариус"},
        ]
    )
    neighbor_id = uuid.uuid4()
    relations = [
        _relation(
            source_id=process.id,
            source_name=process.canonical_name,
            relation_type=NdRelationType.PROCESS_HAS_ROLE,
            target_name="Менеджер",
        ),
        _relation(
            source_id=process.id,
            source_name=process.canonical_name,
            relation_type=NdRelationType.PROCESS_RELATED_TO_PROCESS,
            target_type=NdGraphEntityType.PROCESS,
            target_id=neighbor_id,
            target_name="Согласование договора",
        ),
        _relation(
            source_type=NdGraphEntityType.ROLE,
            source_name="Юрист",
            target_id=process.id,
            target_type=NdGraphEntityType.PROCESS,
            relation_type=NdRelationType.ROLE_RESPONSIBLE_FOR_ACTION,
            evidence_json=[{"quote": "Согласовано?", "action": "Согласовано?"}],
        ),
    ]
    neighbor = ProcessCard(id=neighbor_id, canonical_name="Согласование договора", owner_confirmed=False)
    neighbor_relations = [
        _relation(
            source_id=neighbor_id,
            source_name=neighbor.canonical_name,
            relation_type=NdRelationType.PROCESS_USES_SYSTEM,
            target_type=NdGraphEntityType.SYSTEM,
            target_name="CRM",
        )
    ]

    graph = assemble_process_graph(
        process,
        relations,
        neighbor_processes={neighbor_id: neighbor},
        neighbor_relations={neighbor_id: neighbor_relations},
    )

    assert graph.process_name == process.canonical_name
    assert graph.process_goal == "Обеспечить управление контрактом"
    assert graph.roles == ["Менеджер", "Юрист"]
    assert len(graph.actions) == 4
    assert graph.actions[0].block_type == DiagramBlockType.START
    assert graph.actions[1].block_type == DiagramBlockType.DECISION
    assert graph.actions[2].block_type == DiagramBlockType.DOCUMENT_OUTPUT
    assert graph.actions[3].block_type == DiagramBlockType.END
    assert graph.actions[1].responsible_role == "Юрист"
    assert len(graph.subprocesses) == 1
    assert graph.subprocesses[0].systems == ["CRM"]


def test_assemble_process_graph_loads_smk_sections_from_process_card() -> None:
    process = _process(
        actions_json=[{"action": "Выполнить шаг", "performer": "Менеджер"}],
        effectiveness_criteria_json=[
            {
                "name": "Качество оформления документов",
                "measurement_method": "Претензии работников",
                "reporting_period": "ежеквартально",
            }
        ],
        resources_json=[{"name": "квалифицированный персонал", "type": "personnel"}],
        risks_json=[
            {
                "risk": "Использование неактуальной документированной информации",
                "consequence": "Неправильное оформление документов",
                "control_measure": "Использовать один источник",
                "responsible": "Начальник Управления делами",
            }
        ],
        documentation_and_archive_json=[
            {
                "document": "Оригинал СТО",
                "storage_place": "Архив",
                "responsible": "Специалист по процессному управлению",
            }
        ],
    )

    graph = assemble_process_graph(
        process,
        [],
        neighbor_processes={},
        neighbor_relations={},
    )

    assert len(graph.effectiveness_criteria) == 1
    assert graph.effectiveness_criteria[0].name == "Качество оформления документов"
    assert len(graph.resources) == 1
    assert graph.resources[0].type == "personnel"
    assert len(graph.risks) == 1
    assert graph.risks[0].control_measure == "Использовать один источник"
    assert len(graph.documentation_and_archive) == 1
    assert graph.documentation_and_archive[0].storage_place == "Архив"


def test_attach_source_document_context_from_sto_document() -> None:
    graph = assemble_process_graph(
        _process(),
        [],
        neighbor_processes={},
        neighbor_relations={},
    )
    card = DocumentCard(
        document_id=uuid.uuid4(),
        knowledge_base_id=uuid.uuid4(),
        document_code="СТО-34-003",
        document_type=NdStructuralDocumentType.STO,
    )
    enriched = attach_source_document_context(graph, [card])
    assert enriched.source_document_type == "STO"
    assert enriched.qms_level_label == "Технический"


@pytest.mark.asyncio
async def test_build_process_graph_loads_incoming_relations() -> None:
    process = _process()
    neighbor_id = uuid.uuid4()
    relations = [
        _relation(
            source_id=process.id,
            relation_type=NdRelationType.PROCESS_HAS_ROLE,
            target_name="Роль А",
        ),
        _relation(
            source_id=process.id,
            relation_type=NdRelationType.PROCESS_RELATED_TO_PROCESS,
            target_type=NdGraphEntityType.PROCESS,
            target_id=neighbor_id,
            target_name="Соседний процесс",
        ),
        _relation(
            source_type=NdGraphEntityType.SYSTEM,
            source_name="ERP",
            target_type=NdGraphEntityType.PROCESS,
            target_id=process.id,
            relation_type=NdRelationType.PROCESS_USES_SYSTEM,
        ),
    ]
    neighbor = ProcessCard(id=neighbor_id, canonical_name="Соседний процесс", owner_confirmed=False)
    neighbor_outgoing = [
        _relation(
            source_id=neighbor_id,
            relation_type=NdRelationType.PROCESS_USES_FORM,
            target_type=NdGraphEntityType.FORM,
            target_name="Акт",
        )
    ]

    db = AsyncMock()
    db.get = AsyncMock(return_value=process)
    db.execute = AsyncMock(
        side_effect=[
            MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=relations)))),
            MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[neighbor])))),
            MagicMock(
                scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=neighbor_outgoing)))
            ),
        ]
    )

    graph = await ProcessGraphBuilder(db).build_process_graph(str(process.id))

    assert "Роль А" in graph.roles
    assert "ERP" in graph.systems
    assert graph.subprocesses[0].forms == ["Акт"]


@pytest.mark.asyncio
async def test_build_process_graph_raises_when_missing() -> None:
    db = AsyncMock()
    db.get = AsyncMock(return_value=None)
    with pytest.raises(ProcessGraphBuilderError) as exc:
        await ProcessGraphBuilder(db).build_process_graph(str(uuid.uuid4()))
    assert exc.value.code == "process_not_found"
