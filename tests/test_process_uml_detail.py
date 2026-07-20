from __future__ import annotations

from app.schemas.diagram_block import DiagramBlockType
from app.schemas.nd_process_graph import ProcessGraphActionItem, ProcessGraphDTO
from app.schemas.process_smk_sections import (
    DiagramDetailLevel,
    ProcessDocumentationArchiveItem,
    ProcessEffectivenessCriterionItem,
    ProcessResourceItem,
    ProcessRiskItem,
)
from app.services.process_uml_detail import apply_detail_level_to_context


def _context(**kwargs) -> dict:
    graph = ProcessGraphDTO(
        process_id="p1",
        process_name="Тест",
        actions=[
            ProcessGraphActionItem(id="a1", title="Начало", block_type=DiagramBlockType.START),
            ProcessGraphActionItem(id="a2", title="Операция", block_type=DiagramBlockType.OPERATION),
            ProcessGraphActionItem(id="a3", title="Документ", block_type=DiagramBlockType.DOCUMENT_OUTPUT),
            ProcessGraphActionItem(id="a4", title="Конец", block_type=DiagramBlockType.END),
        ],
        roles=["Менеджер"],
        forms=["Форма"],
        systems=["1С"],
        resources=[ProcessResourceItem(name="Персонал", type="personnel")],
        risks=[ProcessRiskItem(risk="Неактуальная информация", control_measure="Единый источник")],
        effectiveness_criteria=[
            ProcessEffectivenessCriterionItem(
                name="Качество оформления",
                measurement_method="Претензии",
                reporting_period="ежеквартально",
            )
        ],
        documentation_and_archive=[
            ProcessDocumentationArchiveItem(document="Оригинал СТО", storage_place="Архив")
        ],
        process_metadata={
            "resources": [
                {
                    "name": "Персонал",
                    "category": "resources",
                    "graph_item_kind": "metadata",
                    "payload": {"name": "Персонал"},
                }
            ],
            "risks": [
                {
                    "name": "Неактуальная информация",
                    "category": "risks",
                    "graph_item_kind": "metadata",
                    "payload": {"risk": "Неактуальная информация"},
                }
            ],
        },
        **kwargs,
    )
    return {
        "process_graph": graph.model_dump(mode="json"),
        "roles": graph.roles,
        "forms": graph.forms,
        "systems": graph.systems,
    }


def test_compact_hides_risks_resources_and_smk_sections() -> None:
    filtered = apply_detail_level_to_context(_context(), DiagramDetailLevel.COMPACT)
    graph = filtered["process_graph"]

    assert len(graph["actions"]) == 3
    assert graph["actions"][0]["block_type"] == DiagramBlockType.START.value
    assert graph["actions"][1]["block_type"] == DiagramBlockType.OPERATION.value
    assert graph["actions"][2]["block_type"] == DiagramBlockType.END.value
    assert graph["roles"] == []
    assert graph["resources"] == []
    assert graph["risks"] == []
    assert graph["effectiveness_criteria"] == []
    assert graph["documentation_and_archive"] == []
    assert graph["process_metadata"] == {}


def test_standard_hides_smk_sections_but_keeps_roles() -> None:
    filtered = apply_detail_level_to_context(_context(), DiagramDetailLevel.STANDARD)
    graph = filtered["process_graph"]

    assert graph["roles"] == ["Менеджер"]
    assert graph["forms"] == ["Форма"]
    assert graph["resources"] == []
    assert graph["risks"] == []
    assert graph["effectiveness_criteria"] == []
    assert graph["documentation_and_archive"] == []
    assert graph["process_metadata"] == {}


def test_detailed_keeps_smk_sections() -> None:
    filtered = apply_detail_level_to_context(_context(), DiagramDetailLevel.DETAILED)
    graph = filtered["process_graph"]

    assert len(graph["resources"]) == 1
    assert len(graph["risks"]) == 1
    assert len(graph["effectiveness_criteria"]) == 1
    assert len(graph["documentation_and_archive"]) == 1
    assert len(graph["process_metadata"]["resources"]) == 1
    assert filtered["detail_level"] == "detailed"
