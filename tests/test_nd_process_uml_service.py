from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.enums import ConfidenceLevel, NdGraphEntityType, NdRelationExtractionType, NdRelationType
from app.models.nd_control_structural import NdRelation, ProcessCard, ProcessUmlCache
from app.services.nd_process_uml_service import (
    NdProcessUmlService,
    NdProcessUmlServiceError,
    build_process_uml_context,
    compute_content_version,
    extract_mermaid_code,
)


def _process(**kwargs) -> ProcessCard:
    defaults = {
        "id": uuid.uuid4(),
        "canonical_name": "Управление контрактом",
        "description": "Описание",
        "goal": "Цель",
        "owner_candidate": "Начальник отдела",
        "owner_confirmed": False,
        "inputs_json": ["Заявка"],
        "outputs_json": ["Контракт"],
        "actions_json": [{"action": "Согласовать", "performer": "Менеджер"}],
        "roles_json": ["Менеджер"],
        "forms_json": ["Форма заявки"],
        "systems_json": ["1С"],
        "resources_json": [],
        "updated_at": datetime(2026, 6, 17, tzinfo=timezone.utc),
    }
    defaults.update(kwargs)
    return ProcessCard(**defaults)


def _relation(**kwargs) -> NdRelation:
    defaults = {
        "id": uuid.uuid4(),
        "source_type": NdGraphEntityType.PROCESS,
        "source_id": uuid.uuid4(),
        "source_name": "Процесс А",
        "relation_type": NdRelationType.PROCESS_USES_SYSTEM,
        "target_type": NdGraphEntityType.SYSTEM,
        "target_id": None,
        "target_name": "CRM",
        "confidence": ConfidenceLevel.MEDIUM,
        "extraction_type": NdRelationExtractionType.INFERRED,
        "evidence_json": None,
        "is_confirmed": False,
        "updated_at": datetime(2026, 6, 17, tzinfo=timezone.utc),
    }
    defaults.update(kwargs)
    return NdRelation(**defaults)


def test_build_process_uml_context_merges_card_and_relations() -> None:
    process = _process()
    neighbor_id = uuid.uuid4()
    relations = [
        _relation(
            source_id=process.id,
            source_name=process.canonical_name,
            relation_type=NdRelationType.PROCESS_USES_SYSTEM,
            target_type=NdGraphEntityType.SYSTEM,
            target_name="CRM",
        ),
        _relation(
            source_id=process.id,
            source_name=process.canonical_name,
            relation_type=NdRelationType.PROCESS_HAS_ROLE,
            target_type=NdGraphEntityType.ROLE,
            target_name="Юрист",
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
    neighbors = {
        neighbor_id: ProcessCard(
            id=neighbor_id,
            canonical_name="Согласование договора",
            owner_confirmed=False,
        )
    }

    context = build_process_uml_context(process, relations, neighbors)

    assert context["process"]["name"] == process.canonical_name
    assert "Менеджер" in context["actors"]
    assert "Юрист" in context["actors"]
    assert context["steps"][0]["name"] == "Согласовать"
    assert "CRM" in context["systems"]
    assert len(context["related_processes"]) == 1
    assert context["related_processes"][0]["name"] == "Согласование договора"


def test_compute_content_version_changes_when_process_updates() -> None:
    process = _process()
    relations: list[NdRelation] = []
    version_a = compute_content_version(process, relations, [])
    process.goal = "Новая цель"
    version_b = compute_content_version(process, relations, [])
    assert version_a != version_b


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
    process = _process()
    cache = ProcessUmlCache(
        process_id=process.id,
        content_version="abc123",
        uml_type="mermaid_activity",
        uml_code="flowchart TD\n  A --> B",
    )
    db = AsyncMock()
    db.get = AsyncMock(return_value=process)
    db.execute = AsyncMock(
        side_effect=[
            MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))),
            MagicMock(scalar_one_or_none=MagicMock(return_value=cache)),
        ]
    )
    service = NdProcessUmlService(db)

    result = await service.get_process_uml(process.id)

    assert result["cached"] is True
    assert result["uml_code"] == cache.uml_code
    assert result["uml_type"] == "mermaid_activity"


@pytest.mark.asyncio
async def test_get_process_uml_raises_when_process_missing() -> None:
    db = AsyncMock()
    db.get = AsyncMock(return_value=None)
    service = NdProcessUmlService(db)

    with pytest.raises(NdProcessUmlServiceError) as exc:
        await service.get_process_uml(uuid.uuid4())

    assert exc.value.code == "process_not_found"


@pytest.mark.asyncio
async def test_get_process_uml_calls_llm_on_cache_miss() -> None:
    process = _process()
    db = AsyncMock()
    db.get = AsyncMock(return_value=process)
    db.execute = AsyncMock(
        side_effect=[
            MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))),
            MagicMock(scalar_one_or_none=MagicMock(return_value=None)),
        ]
    )
    db.add = MagicMock()
    db.flush = AsyncMock()

    async def fake_llm(messages, **kwargs):
        return {"choices": [{"message": {"content": "flowchart TD\n  Start --> End"}}]}

    service = NdProcessUmlService(db, llm_chat=fake_llm)
    result = await service.get_process_uml(process.id)

    assert result["cached"] is False
    assert "flowchart TD" in result["uml_code"]
    db.add.assert_called_once()
