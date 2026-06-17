from __future__ import annotations

import json
import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agents.nd_control_agent.document_context import DocumentContext
from app.agents.nd_control_agent.knowledge_base_access_schemas import (
    KnowledgeBaseDocumentMetadata,
    KnowledgeBaseDocumentTextResult,
)
from app.agents.nd_control_agent.knowledge_base_access_service import KnowledgeBaseAccessServiceError
from app.models.enums import NdExtractionStatus, NdRelationType
from app.models.nd_control_structural import DocumentCard, NdRelation, ProcessCard
from app.services.nd_document_card_extraction_service import NdDocumentCardExtractionService


def _valid_llm_payload() -> dict:
    return {
        "document": {
            "document_code": "И-15-230",
            "title": "Инструкция",
            "document_type": "instruction",
            "version": "1",
            "status": "active",
            "purpose": "Регламентирует входной контроль",
            "scope": {"text": "Для всех подразделений", "departments": ["ОТК"], "positions": [], "applies_to_all_company": False},
        },
        "participants": {"developed_by": [], "checked_by": [], "approved_by": [], "agreed_by": []},
        "processes": [
            {
                "name": "Входной контроль",
                "description": "Проверка материалов",
                "goal": "Исключить брак",
                "inputs": ["Партия"],
                "outputs": ["Акт"],
                "actions": [{"action": "Проверить сертификат", "performer": "Инженер ОТК"}],
                "roles": ["Инженер ОТК"],
                "forms": ["Акт входного контроля"],
                "systems": ["1С"],
                "resources": ["Склад"],
                "related_departments": ["ОТК"],
                "owner_candidates": [],
            }
        ],
        "responsibilities": [],
        "forms": [{"name": "Акт входного контроля", "code": "Ф-12"}],
        "related_departments": ["ОТК"],
        "related_documents": [],
        "related_systems": ["1С"],
        "unknowns": [],
    }


def _metadata(document_id: uuid.UUID | None = None, kb_id: uuid.UUID | None = None) -> KnowledgeBaseDocumentMetadata:
    return KnowledgeBaseDocumentMetadata(
        document_id=document_id or uuid.uuid4(),
        knowledge_base_id=kb_id or uuid.uuid4(),
        file_name="STO.pdf",
        title="Инструкция",
        parse_status="ready",
        size=100,
        created_at=None,
        updated_at=None,
        extra={},
    )


def _llm_response(payload: dict) -> dict:
    return {"choices": [{"message": {"content": json.dumps(payload, ensure_ascii=False)}}]}


@pytest.fixture
def db() -> AsyncMock:
    session = AsyncMock()
    session.add = MagicMock()
    return session


@pytest.fixture
def kb_access() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def service(db: AsyncMock, kb_access: AsyncMock) -> NdDocumentCardExtractionService:
    return NdDocumentCardExtractionService(db, kb_access=kb_access, llm_chat=AsyncMock())


def _mock_no_existing_card(db: AsyncMock) -> None:
    execute_result = MagicMock()
    execute_result.scalar_one_or_none.return_value = None
    db.execute = AsyncMock(return_value=execute_result)


def _mock_existing_document_card(db: AsyncMock, card: DocumentCard) -> None:
    async def _execute(_stmt: object) -> MagicMock:
        result = MagicMock()
        stmt_text = str(_stmt)
        if "nd_structural_document_cards" in stmt_text:
            result.scalar_one_or_none.return_value = card
        else:
            result.scalar_one_or_none.return_value = None
        return result

    db.execute = AsyncMock(side_effect=_execute)


@pytest.mark.asyncio
async def test_extract_with_text_and_valid_llm_creates_document_card(
    service: NdDocumentCardExtractionService,
    db: AsyncMock,
    kb_access: AsyncMock,
) -> None:
    metadata = _metadata()
    kb_access.get_document_metadata = AsyncMock(return_value=metadata)
    kb_access.get_document_text = AsyncMock(
        return_value=KnowledgeBaseDocumentTextResult(
            document_id=metadata.document_id,
            text="Полный текст документа",
            status="ok",
            source="extracted_text",
        )
    )
    _mock_no_existing_card(db)
    service._llm_chat.return_value = _llm_response(_valid_llm_payload())

    card = await service.extract_document_card(str(metadata.document_id))

    assert card.extraction_status == NdExtractionStatus.COMPLETED
    assert card.document_code == "И-15-230"
    assert card.title == "Инструкция"
    assert card.raw_extracted_json is not None
    assert db.add.called


@pytest.mark.asyncio
async def test_extract_without_text_sets_needs_review(
    service: NdDocumentCardExtractionService,
    db: AsyncMock,
    kb_access: AsyncMock,
) -> None:
    metadata = _metadata()
    kb_access.get_document_metadata = AsyncMock(return_value=metadata)
    kb_access.get_document_text = AsyncMock(
        return_value=KnowledgeBaseDocumentTextResult(
            document_id=metadata.document_id,
            text="",
            status="empty",
            source="none",
            message="empty",
        )
    )
    kb_access.get_document_chunks = AsyncMock(
        side_effect=KnowledgeBaseAccessServiceError("Чанки документа не найдены", code="chunks_not_found")
    )
    _mock_no_existing_card(db)

    card = await service.extract_document_card(str(metadata.document_id))

    assert card.extraction_status == NdExtractionStatus.NEEDS_REVIEW
    assert card.raw_extracted_json["error"] == "empty_text"
    service._llm_chat.assert_not_called()


@pytest.mark.asyncio
async def test_invalid_llm_json_sets_failed(
    service: NdDocumentCardExtractionService,
    db: AsyncMock,
    kb_access: AsyncMock,
) -> None:
    metadata = _metadata()
    kb_access.get_document_metadata = AsyncMock(return_value=metadata)
    kb_access.get_document_text = AsyncMock(
        return_value=KnowledgeBaseDocumentTextResult(
            document_id=metadata.document_id,
            text="Текст",
            status="ok",
            source="extracted_text",
        )
    )
    _mock_no_existing_card(db)
    service._llm_chat.return_value = {"choices": [{"message": {"content": "not-json"}}]}

    card = await service.extract_document_card(str(metadata.document_id))

    assert card.extraction_status == NdExtractionStatus.FAILED
    assert "LLM" in card.raw_extracted_json["error"]


@pytest.mark.asyncio
async def test_process_card_created_from_extraction(
    service: NdDocumentCardExtractionService,
    db: AsyncMock,
    kb_access: AsyncMock,
) -> None:
    metadata = _metadata()
    kb_access.get_document_metadata = AsyncMock(return_value=metadata)
    kb_access.get_document_text = AsyncMock(
        return_value=KnowledgeBaseDocumentTextResult(
            document_id=metadata.document_id,
            text="Текст",
            status="ok",
            source="extracted_text",
        )
    )
    _mock_no_existing_card(db)
    service._llm_chat.return_value = _llm_response(_valid_llm_payload())

    await service.extract_document_card(str(metadata.document_id))

    added_processes = [call.args[0] for call in db.add.call_args_list if isinstance(call.args[0], ProcessCard)]
    assert len(added_processes) == 1
    assert added_processes[0].canonical_name == "Входной контроль"


@pytest.mark.asyncio
async def test_relations_created_from_process(
    service: NdDocumentCardExtractionService,
    db: AsyncMock,
    kb_access: AsyncMock,
) -> None:
    metadata = _metadata()
    kb_access.get_document_metadata = AsyncMock(return_value=metadata)
    kb_access.get_document_text = AsyncMock(
        return_value=KnowledgeBaseDocumentTextResult(
            document_id=metadata.document_id,
            text="Текст",
            status="ok",
            source="extracted_text",
        )
    )
    _mock_no_existing_card(db)
    service._llm_chat.return_value = _llm_response(_valid_llm_payload())

    await service.extract_document_card(str(metadata.document_id))

    relations = [call.args[0] for call in db.add.call_args_list if isinstance(call.args[0], NdRelation)]
    relation_types = {relation.relation_type for relation in relations}
    assert NdRelationType.DOCUMENT_REGULATES_PROCESS in relation_types
    assert NdRelationType.PROCESS_HAS_ROLE in relation_types
    assert NdRelationType.PROCESS_USES_FORM in relation_types
    assert all(relation.is_confirmed is False for relation in relations)
    assert all(relation.evidence_json is not None for relation in relations if relation.relation_type == NdRelationType.DOCUMENT_REGULATES_PROCESS)


@pytest.mark.asyncio
async def test_repeated_run_does_not_duplicate_document_card(
    service: NdDocumentCardExtractionService,
    db: AsyncMock,
    kb_access: AsyncMock,
) -> None:
    metadata = _metadata()
    existing = DocumentCard(
        id=uuid.uuid4(),
        document_id=metadata.document_id,
        knowledge_base_id=metadata.knowledge_base_id,
        extraction_status=NdExtractionStatus.COMPLETED,
        extraction_confidence=Decimal("0.9000"),
    )
    kb_access.get_document_metadata = AsyncMock(return_value=metadata)
    kb_access.get_document_text = AsyncMock(
        return_value=KnowledgeBaseDocumentTextResult(
            document_id=metadata.document_id,
            text="Текст",
            status="ok",
            source="extracted_text",
        )
    )
    _mock_existing_document_card(db, existing)
    service._llm_chat.return_value = _llm_response(_valid_llm_payload())

    card = await service.extract_document_card(str(metadata.document_id))

    assert card.id == existing.id
    document_card_adds = [
        call.args[0] for call in db.add.call_args_list if isinstance(call.args[0], DocumentCard)
    ]
    assert document_card_adds == []


@pytest.mark.asyncio
async def test_build_document_context_full_text_mode(
    service: NdDocumentCardExtractionService,
    kb_access: AsyncMock,
) -> None:
    doc_id = str(uuid.uuid4())
    kb_access.get_document_text = AsyncMock(
        return_value=KnowledgeBaseDocumentTextResult(
            document_id=uuid.UUID(doc_id),
            text="Короткий текст",
            status="ok",
            source="extracted_text",
        )
    )

    context = await service.build_document_context(doc_id)

    assert context.mode == "full_text"
    assert context.full_text == "Короткий текст"
    assert context.total_chunks == 0


@pytest.mark.asyncio
async def test_build_document_context_chunked_mode(
    service: NdDocumentCardExtractionService,
    kb_access: AsyncMock,
) -> None:
    from app.agents.nd_control_agent import config
    from app.agents.nd_control_agent.knowledge_base_access_schemas import KnowledgeBaseDocumentChunk

    doc_id = uuid.uuid4()
    huge_text = "A" * (config.ND_EXTRACTION_FULL_TEXT_MAX_CHARS + 100)
    kb_access.get_document_text = AsyncMock(
        return_value=KnowledgeBaseDocumentTextResult(
            document_id=doc_id,
            text=huge_text,
            status="ok",
            source="extracted_text",
        )
    )
    kb_access.get_document_chunks = AsyncMock(
        return_value=[
            KnowledgeBaseDocumentChunk(
                chunk_id=uuid.uuid4(),
                document_id=doc_id,
                text="Фрагмент 1",
                page_number=1,
                section="1",
                metadata={"chunk_index": 0},
            )
        ]
    )

    context = await service.build_document_context(str(doc_id))

    assert context.mode == "chunked"
    assert context.total_chunks == 1
    assert context.chunks[0].page_number == 1
