from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.nd_control_structural import ProcessUmlCache
from app.schemas.nd_process_graph import ProcessGraphDTO, ProcessGraphStepItem, ProcessSubprocessItem
from app.services.nd_process_uml_service import (
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
        "actors": [],
        "steps": [],
        "inputs": [],
        "outputs": [],
        "systems": [],
        "forms": [],
        "subprocesses": [],
    }
    defaults.update(kwargs)
    return ProcessGraphDTO(**defaults)


def test_compute_content_version_changes_when_graph_updates() -> None:
    graph_a = _graph(inputs=["A"])
    graph_b = _graph(inputs=["B"])
    assert compute_content_version(graph_a) != compute_content_version(graph_b)


def test_extract_mermaid_code_from_codeblock() -> None:
    content = "```mermaid\nflowchart TD\n  A[Старт] --> B[Конец]\n```"
    assert "flowchart TD" in extract_mermaid_code(content)


def test_extract_mermaid_code_from_json_payload() -> None:
    content = '{"uml_code": "flowchart TD\\n  A --> B"}'
    assert extract_mermaid_code(content).startswith("flowchart TD")


def test_extract_mermaid_code_rejects_invalid() -> None:
    with pytest.raises(NdProcessUmlServiceError):
        extract_mermaid_code("not a diagram")


@pytest.mark.asyncio
async def test_get_process_uml_returns_cache_hit() -> None:
    process_id = uuid.uuid4()
    graph = _graph(process_id=str(process_id), steps=[ProcessGraphStepItem(name="Шаг")])
    content_version = compute_content_version(graph)
    cache = ProcessUmlCache(
        process_id=process_id,
        content_version=content_version,
        uml_type="mermaid_activity",
        uml_code="flowchart TD\n  A --> B",
    )

    graph_builder = AsyncMock()
    graph_builder.build_process_graph = AsyncMock(return_value=graph)
    db = AsyncMock()
    db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=cache)))
    service = NdProcessUmlService(db, graph_builder=graph_builder)

    result = await service.get_process_uml(process_id)

    assert result["cached"] is True
    assert result["uml_code"] == cache.uml_code


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
async def test_get_process_uml_calls_llm_on_cache_miss() -> None:
    process_id = uuid.uuid4()
    graph = _graph(
        process_id=str(process_id),
        process_name="Тест",
        steps=[ProcessGraphStepItem(name="Шаг 1")],
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

    async def fake_llm(messages, **kwargs):
        return {"choices": [{"message": {"content": "flowchart TD\n  Start --> End"}}]}

    service = NdProcessUmlService(db, graph_builder=graph_builder, llm_chat=fake_llm)
    result = await service.get_process_uml(process_id)

    assert result["cached"] is False
    assert "flowchart TD" in result["uml_code"]
    db.add.assert_called_once()
