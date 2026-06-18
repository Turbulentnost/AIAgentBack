from __future__ import annotations

import uuid

from app.schemas.diagram_block import DiagramBlockType
from app.schemas.nd_process_graph import ProcessGraphActionItem, ProcessGraphDTO
from app.services.process_mermaid_validator import validate_process_mermaid


def _valid_mermaid() -> str:
    return (
        "flowchart TD\n"
        "  subgraph lane_role[\"Менеджер\"]\n"
        "    start_node([Начало]) --> op1[\"Операция\"]\n"
        "    op1 --> dec1{\"Согласовано?\"}\n"
        "    dec1 -->|Да| doc1[\"Документ: Извещение\"]\n"
        "    dec1 -->|Нет| op1\n"
        "    doc1 --> end_node([Конец])\n"
        "  end"
    )


def _graph(**kwargs) -> ProcessGraphDTO:
    defaults = {
        "process_id": str(uuid.uuid4()),
        "process_name": "Тест",
        "roles": ["Менеджер"],
        "documents": ["Извещение"],
        "forms": ["Форма заявки"],
        "outputs": ["Отчёт"],
        "conditions": ["Согласовано?"],
        "actions": [
            ProcessGraphActionItem(id="a1", title="Начало", block_type=DiagramBlockType.START),
            ProcessGraphActionItem(id="a2", title="Согласовано?", block_type=DiagramBlockType.DECISION),
            ProcessGraphActionItem(
                id="a3",
                title="Извещение",
                block_type=DiagramBlockType.DOCUMENT_OUTPUT,
            ),
            ProcessGraphActionItem(id="a4", title="Конец", block_type=DiagramBlockType.END),
        ],
    }
    defaults.update(kwargs)
    return ProcessGraphDTO(**defaults)


def test_validate_process_mermaid_accepts_valid_diagram() -> None:
    result = validate_process_mermaid(_valid_mermaid(), _graph())
    assert result.is_valid
    assert result.status in {"valid", "warning"}


def test_validate_process_mermaid_fails_without_start_end() -> None:
    code = "flowchart TD\n  step1[\"Операция\"] --> step2[\"Другая операция\"]"
    result = validate_process_mermaid(code, _graph())
    assert not result.is_valid
    assert any("началь" in item.lower() or "конеч" in item.lower() for item in result.errors + result.warnings)


def test_validate_process_mermaid_fails_when_start_is_not_connected_to_first_action() -> None:
    code = (
        "flowchart TD\n"
        "  subgraph lane_role[\"Менеджер\"]\n"
        "    start_node([Начало])\n"
        "    op1[\"Операция\"] --> end_node([Конец])\n"
        "  end"
    )
    result = validate_process_mermaid(code, _graph(conditions=[]))
    assert not result.is_valid
    assert any("несвязанные" in item.lower() or "исходящей" in item.lower() for item in result.errors)


def test_validate_process_mermaid_fails_on_orphan_resources_node() -> None:
    code = (
        "flowchart TD\n"
        "  subgraph lane_role[\"Менеджер\"]\n"
        "    start_node([Начало]) --> op1[\"Операция\"]\n"
        "    op1 --> doc1[\"Документ: Отчёт\"] --> end_node([Конец])\n"
        "  end\n"
        "  resources[\"Ресурсы процесса\"]"
    )
    result = validate_process_mermaid(code, _graph(conditions=[]))
    assert not result.is_valid
    assert any("resources" in item for item in result.orphan_nodes)


def test_validate_process_mermaid_fails_on_orphan_criteria_node() -> None:
    code = (
        "flowchart TD\n"
        "  subgraph lane_role[\"Менеджер\"]\n"
        "    start_node([Начало]) --> op1[\"Операция\"]\n"
        "    op1 --> doc1[\"Документ: Отчёт\"] --> end_node([Конец])\n"
        "  end\n"
        "  criteria[\"Критерии результативности\"]"
    )
    result = validate_process_mermaid(code, _graph(conditions=[]))
    assert not result.is_valid
    assert any("criteria" in item for item in result.orphan_nodes)


def test_validate_process_mermaid_accepts_detailed_reference_nodes_when_linked() -> None:
    code = (
        "flowchart TD\n"
        "  subgraph lane_role[\"Менеджер\"]\n"
        "    start_node([Начало]) --> op1[\"Операция\"]\n"
        "    op1 --> doc1[\"Документ: Отчёт\"] --> end_node([Конец])\n"
        "  end\n"
        "  subgraph info[\"Справочная информация\"]\n"
        "    resources[\"Ресурсы процесса\"]\n"
        "    criteria[\"Критерии результативности\"]\n"
        "  end\n"
        "  op1 -.-> resources\n"
        "  op1 -.-> criteria"
    )
    result = validate_process_mermaid(code, _graph(conditions=[]))
    assert result.is_valid or result.status == "warning"
    assert result.orphan_nodes == []


def test_validate_process_mermaid_fails_on_uuid_and_markdown() -> None:
    code = (
        "```mermaid\n"
        "flowchart TD\n"
        f"  start_node([Начало]) --> {uuid.uuid4()}\n"
        "```"
    )
    result = validate_process_mermaid(code, _graph())
    assert not result.is_valid
    assert any("UUID" in item or "markdown" in item.lower() for item in result.errors)


def test_validate_process_mermaid_requires_subgraph_for_roles() -> None:
    code = "flowchart TD\n  start_node([Начало]) --> end_node([Конец])"
    result = validate_process_mermaid(code, _graph())
    assert not result.is_valid
    assert any("subgraph" in item.lower() for item in result.errors)


def test_validate_process_mermaid_policy_without_actions_does_not_require_start_end() -> None:
    code = (
        "flowchart TD\n"
        "  intent[\"Намерение\"] --> scope[\"Область распространения\"]\n"
        "  scope --> obligations[\"Обязательства\"]\n"
        "  obligations --> responsibility[\"Ответственность\"]"
    )
    graph = _graph(
        actions=[],
        roles=["Директор"],
        conditions=[],
        source_document_type="POLICY",
        primary_document_type="POLICY",
    )
    result = validate_process_mermaid(code, graph)
    assert result.is_valid or result.status == "warning"
    assert not any("началь" in item.lower() for item in result.errors)


def test_validate_process_mermaid_regulation_requires_operations_and_roles() -> None:
    code = "flowchart TD\n  start_node([Начало]) --> end_node([Конец])"
    graph = _graph(
        source_document_type="PROCESS_REGULATION",
        primary_document_type="PROCESS_REGULATION",
        conditions=["Согласовано?"],
    )
    result = validate_process_mermaid(code, graph)
    assert not result.is_valid
    assert any("операц" in item.lower() or "рол" in item.lower() or "decision" in item.lower() for item in result.errors)


def test_validate_process_mermaid_sto_warns_when_smk_data_missing_in_diagram() -> None:
    code = (
        "flowchart TD\n"
        "  subgraph lane_role[\"Менеджер\"]\n"
        "    start_node([Начало]) --> op1[\"Операция\"]\n"
        "    op1 --> end_node([Конец])\n"
        "  end"
    )
    from app.schemas.process_smk_sections import ProcessEffectivenessCriterionItem, ProcessRiskItem

    graph = _graph(
        source_document_type="STO",
        primary_document_type="STO",
        effectiveness_criteria=[ProcessEffectivenessCriterionItem(name="Качество")],
        risks=[ProcessRiskItem(risk="Неактуальная информация")],
    )
    result = validate_process_mermaid(code, graph)
    assert any("критер" in item.lower() or "риск" in item.lower() for item in result.warnings)
