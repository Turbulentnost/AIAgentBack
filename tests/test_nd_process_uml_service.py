from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.nd_control_structural import ProcessUmlCache
from app.schemas.diagram_block import DiagramBlockType
from app.schemas.nd_process_graph import ProcessGraphActionItem, ProcessGraphDTO, ProcessSubprocessItem
from app.schemas.process_smk_sections import DiagramDetailLevel
from app.services.nd_process_uml_service import (
    UML_GENERATOR_VERSION,
    NdProcessUmlService,
    NdProcessUmlServiceError,
    compute_content_version,
    extract_mermaid_code,
)
from app.services.process_graph_builder import ProcessGraphBuilderError


def _graph(**kwargs) -> ProcessGraphDTO:
    defaults = {
        "process_id": str(uuid.uuid4()),
        "process_name": "Тест",
        "roles": [],
        "actions": [],
        "inputs": [],
        "outputs": [],
        "systems": [],
        "forms": [],
        "subprocesses": [],
    }
    defaults.update(kwargs)
    return ProcessGraphDTO(**defaults)


def test_compute_content_version_changes_when_graph_updates() -> None:
    from app.schemas.process_smk_sections import DiagramDetailLevel

    graph_a = _graph(inputs=["A"])
    graph_b = _graph(inputs=["B"])
    assert compute_content_version(graph_a, detail_level=DiagramDetailLevel.STANDARD) != compute_content_version(
        graph_b, detail_level=DiagramDetailLevel.STANDARD
    )


def test_compute_content_version_changes_when_detail_level_changes() -> None:
    from app.schemas.process_smk_sections import DiagramDetailLevel

    graph = _graph(inputs=["A"])
    standard = compute_content_version(graph, detail_level=DiagramDetailLevel.STANDARD)
    detailed = compute_content_version(graph, detail_level=DiagramDetailLevel.DETAILED)
    assert standard != detailed


def test_compute_content_version_changes_when_generator_version_changes() -> None:
    from app.schemas.process_smk_sections import DiagramDetailLevel

    graph = _graph(inputs=["A"])
    version_a = compute_content_version(graph, detail_level=DiagramDetailLevel.STANDARD)
    import app.services.nd_process_uml_service as module

    original = module.UML_GENERATOR_VERSION
    module.UML_GENERATOR_VERSION = "3.0.0-sto"
    try:
        version_b = compute_content_version(graph, detail_level=DiagramDetailLevel.STANDARD)
    finally:
        module.UML_GENERATOR_VERSION = original
    assert version_a != version_b


def test_extract_mermaid_code_from_codeblock() -> None:
    content = "```mermaid\nflowchart TD\n  start_node([Начало]) --> end_node([Конец])\n```"
    assert "flowchart TD" in extract_mermaid_code(content)


def test_extract_mermaid_code_from_json_payload() -> None:
    content = '{"uml_code": "flowchart TD\\n  start_node([Начало]) --> end_node([Конец])"}'
    assert extract_mermaid_code(content).startswith("flowchart TD")


def test_extract_mermaid_code_rejects_invalid() -> None:
    with pytest.raises(NdProcessUmlServiceError):
        extract_mermaid_code("not a diagram")


@pytest.mark.asyncio
async def test_get_process_uml_returns_cache_hit() -> None:
    process_id = uuid.uuid4()
    graph = _graph(
        process_id=str(process_id),
        actions=[ProcessGraphActionItem(id="a1", title="Шаг", block_type=DiagramBlockType.OPERATION)],
    )
    content_version = compute_content_version(graph, detail_level=DiagramDetailLevel.STANDARD)
    cache = ProcessUmlCache(
        process_id=process_id,
        content_version=content_version,
        uml_type="mermaid_flowchart_sto",
        uml_code="flowchart TD\n  start_node([Начало]) --> end_node([Конец])",
        generator_version=UML_GENERATOR_VERSION,
        standard_profile="STO-34-003_GOST-19.701-90",
        validation_status="valid",
        validation_errors=[],
        detail_level="standard",
    )

    graph_builder = AsyncMock()
    graph_builder.build_process_graph = AsyncMock(return_value=graph)
    db = AsyncMock()
    db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=cache)))
    service = NdProcessUmlService(db, graph_builder=graph_builder)

    result = await service.get_process_uml(process_id)

    assert result["cached"] is True
    assert result["validation_status"] == "valid"
    assert result["detail_level"] == "standard"
    assert "flowchart TD" in result["uml_code"]


@pytest.mark.asyncio
async def test_get_process_uml_passes_detail_level_to_prompt() -> None:
    process_id = uuid.uuid4()
    graph = _graph(
        process_id=str(process_id),
        actions=[ProcessGraphActionItem(id="a1", title="Шаг", block_type=DiagramBlockType.OPERATION)],
    )

    graph_builder = AsyncMock()
    graph_builder.build_process_graph = AsyncMock(return_value=graph)
    db = AsyncMock()
    db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None)))
    db.add = MagicMock()
    db.flush = AsyncMock()

    captured: dict = {}

    async def fake_llm(messages, **kwargs):
        captured["user_prompt"] = messages[-1]["content"]
        return {
            "choices": [
                {
                    "message": {
                        "content": "flowchart TD\n  start_node([Начало]) --> end_node([Конец])"
                    }
                }
            ]
        }

    service = NdProcessUmlService(db, graph_builder=graph_builder, llm_chat=fake_llm)
    result = await service.get_process_uml(process_id, detail_level="detailed")

    assert result["detail_level"] == "detailed"
    assert "Режим detailed" in captured["user_prompt"]


@pytest.mark.asyncio
async def test_get_process_uml_raises_when_process_missing() -> None:
    graph_builder = AsyncMock()
    graph_builder.build_process_graph = AsyncMock(
        side_effect=ProcessGraphBuilderError("Процесс не найден", code="process_not_found")
    )
    service = NdProcessUmlService(AsyncMock(), graph_builder=graph_builder)

    with pytest.raises(NdProcessUmlServiceError) as exc:
        await service.get_process_uml(uuid.uuid4())

    assert exc.value.code == "process_not_found"


@pytest.mark.asyncio
async def test_get_process_uml_calls_llm_and_retries_on_invalid_mermaid() -> None:
    process_id = uuid.uuid4()
    graph = _graph(
        process_id=str(process_id),
        process_name="Тест",
        roles=["Менеджер"],
        actions=[
            ProcessGraphActionItem(id="a1", title="Начало", block_type=DiagramBlockType.START),
            ProcessGraphActionItem(id="a2", title="Конец", block_type=DiagramBlockType.END),
        ],
        conditions=["Согласовано?"],
        subprocesses=[
            ProcessSubprocessItem(
                name="Сосед",
                relation_type="PROCESS_RELATED_TO_PROCESS",
                relation_type_label="Связан",
                direction="outgoing",
            )
        ],
    )

    graph_builder = AsyncMock()
    graph_builder.build_process_graph = AsyncMock(return_value=graph)
    db = AsyncMock()
    db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None)))
    db.add = MagicMock()
    db.flush = AsyncMock()

    responses = [
        "flowchart TD\n  step1[Шаг] --> step2[Конец]",
        (
            "flowchart TD\n"
            "  subgraph lane_mgr[\"Менеджер\"]\n"
            "    start_node([Начало]) --> dec1{\"Согласовано?\"}\n"
            "    dec1 -->|Да| end_node([Конец])\n"
            "    dec1 -->|Нет| start_node\n"
            "  end"
        ),
    ]
    call_index = {"value": 0}

    async def fake_llm(messages, **kwargs):
        content = responses[min(call_index["value"], len(responses) - 1)]
        call_index["value"] += 1
        return {"choices": [{"message": {"content": content}}]}

    service = NdProcessUmlService(db, graph_builder=graph_builder, llm_chat=fake_llm)
    result = await service.get_process_uml(process_id)

    assert result["cached"] is False
    assert call_index["value"] == 2
    assert result["validation_status"] in {"valid", "warning"}
    assert "flowchart TD" in result["uml_code"]
    db.add.assert_called_once()


@pytest.mark.asyncio
async def test_get_process_uml_retries_when_orphan_nodes_found() -> None:
    process_id = uuid.uuid4()
    graph = _graph(
        process_id=str(process_id),
        process_name="Тест",
        roles=["Менеджер"],
        actions=[
            ProcessGraphActionItem(id="a1", title="Начало", block_type=DiagramBlockType.START),
            ProcessGraphActionItem(id="a2", title="Операция", block_type=DiagramBlockType.OPERATION),
            ProcessGraphActionItem(id="a3", title="Конец", block_type=DiagramBlockType.END),
        ],
    )

    graph_builder = AsyncMock()
    graph_builder.build_process_graph = AsyncMock(return_value=graph)
    db = AsyncMock()
    db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None)))
    db.add = MagicMock()
    db.flush = AsyncMock()

    responses = [
        (
            "flowchart TD\n"
            "  subgraph lane_mgr[\"Менеджер\"]\n"
            "    start_node([Начало]) --> op1[\"Операция\"] --> end_node([Конец])\n"
            "  end\n"
            "  resources[\"Ресурсы процесса\"]"
        ),
        (
            "flowchart TD\n"
            "  subgraph lane_mgr[\"Менеджер\"]\n"
            "    start_node([Начало]) --> op1[\"Операция\"] --> end_node([Конец])\n"
            "  end\n"
            "  subgraph info[\"Справочная информация\"]\n"
            "    resources[\"Ресурсы процесса\"]\n"
            "  end\n"
            "  op1 -.-> resources"
        ),
    ]
    prompts: list[str] = []
    call_index = {"value": 0}

    async def fake_llm(messages, **kwargs):
        prompts.append(messages[-1]["content"])
        content = responses[min(call_index["value"], len(responses) - 1)]
        call_index["value"] += 1
        return {"choices": [{"message": {"content": content}}]}

    service = NdProcessUmlService(db, graph_builder=graph_builder, llm_chat=fake_llm)
    result = await service.get_process_uml(process_id)

    assert call_index["value"] == 2
    assert result["validation_status"] in {"valid", "warning"}
    assert "resources" in prompts[-1]
    assert "несвязанные узлы" in prompts[-1]
