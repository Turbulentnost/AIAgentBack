from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.enums import ConfidenceLevel, NdGraphEntityType, NdRelationExtractionType, NdRelationType
from app.models.nd_control_structural import NdRelation, ProcessCard
from app.services.process_graph_builder import (
    ProcessGraphBuilder,
    ProcessGraphBuilderError,
    assemble_process_graph,
    extract_graph_fragments,
)


def _process(**kwargs) -> ProcessCard:
    defaults = {
        "id": uuid.uuid4(),
        "canonical_name": "Управление контрактом",
        "owner_confirmed": False,
        "actions_json": [{"action": "Согласовать", "performer": "Менеджер"}],
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
    ]

    fragments = extract_graph_fragments(relations, process_id=process_id)

    assert fragments["actors"] == ["Юрист"]
    assert fragments["inputs"] == ["Заявка"]
    assert fragments["outputs"] == ["Контракт"]
    assert fragments["systems"] == ["1С"]
    assert fragments["forms"] == ["Форма заявки"]


def test_assemble_process_graph_with_one_hop_subprocesses() -> None:
    process = _process()
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
    assert graph.actors == ["Менеджер"]
    assert len(graph.steps) == 1
    assert graph.steps[0].name == "Согласовать"
    assert len(graph.subprocesses) == 1
    assert graph.subprocesses[0].name == "Согласование договора"
    assert graph.subprocesses[0].systems == ["CRM"]


@pytest.mark.asyncio
async def test_build_process_graph_loads_outgoing_relations_only() -> None:
    process = _process()
    neighbor_id = uuid.uuid4()
    outgoing = [
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
    ]
    incoming_ignored = _relation(
        source_id=uuid.uuid4(),
        target_type=NdGraphEntityType.PROCESS,
        target_id=process.id,
        relation_type=NdRelationType.PROCESS_USES_SYSTEM,
        target_name="Не должен попасть",
    )
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
            MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=outgoing)))),
            MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[neighbor])))),
            MagicMock(
                scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=neighbor_outgoing)))
            ),
        ]
    )

    graph = await ProcessGraphBuilder(db).build_process_graph(str(process.id))

    assert graph.actors == ["Роль А"]
    assert graph.systems == []
    assert graph.subprocesses[0].forms == ["Акт"]
    assert incoming_ignored.target_name not in graph.systems


@pytest.mark.asyncio
async def test_build_process_graph_raises_when_missing() -> None:
    db = AsyncMock()
    db.get = AsyncMock(return_value=None)
    with pytest.raises(ProcessGraphBuilderError) as exc:
        await ProcessGraphBuilder(db).build_process_graph(str(uuid.uuid4()))
    assert exc.value.code == "process_not_found"
