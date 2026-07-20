from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.nd_control_agent.knowledge_base_access_service import (
    KnowledgeBaseAccessService,
    KnowledgeBaseAccessServiceError,
)
from app.models.document import Document, DocumentChunk, DocumentVersion
from app.models.enums import KnowledgeBaseSourceStatus, TextExtractStatus
from app.models.knowledge_base import KnowledgeBase, KnowledgeBaseSource
from app.schemas.knowledge_base import KnowledgeBaseSearchHit, KnowledgeBaseTestSearchResponse
from app.services.knowledge_base_service import KnowledgeBaseServiceError


def _make_document(**overrides) -> Document:
    doc_id = overrides.pop("id", uuid.uuid4())
    return Document(
        id=doc_id,
        title=overrides.pop("title", "СТО-001"),
        original_filename=overrides.pop("original_filename", "STO-001.pdf"),
        file_size=overrides.pop("file_size", 1024),
        text_extract_status=overrides.pop("text_extract_status", TextExtractStatus.EXTRACTED),
        created_at=overrides.pop("created_at", datetime.now(timezone.utc)),
        updated_at=overrides.pop("updated_at", datetime.now(timezone.utc)),
        **overrides,
    )


def _make_source(*, kb_id: uuid.UUID, document: Document, version_id: uuid.UUID) -> KnowledgeBaseSource:
    return KnowledgeBaseSource(
        id=uuid.uuid4(),
        knowledge_base_id=kb_id,
        document_id=document.id,
        document_version_id=version_id,
        processing_status=KnowledgeBaseSourceStatus.READY,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )


@pytest.mark.asyncio
async def test_list_documents_returns_items() -> None:
    kb_id = uuid.uuid4()
    document = _make_document()
    version_id = uuid.uuid4()
    source = _make_source(kb_id=kb_id, document=document, version_id=version_id)

    db = AsyncMock()
    service = KnowledgeBaseAccessService(db)
    service._kb_service.get_or_raise = AsyncMock(return_value=MagicMock(id=kb_id))

    execute_result = MagicMock()
    execute_result.all.return_value = [(source, document)]
    db.execute = AsyncMock(return_value=execute_result)

    items = await service.list_documents(str(kb_id))

    assert len(items) == 1
    assert items[0].document_id == document.id
    assert items[0].knowledge_base_id == kb_id
    assert items[0].file_name == "STO-001.pdf"
    assert items[0].parse_status == "ready"


@pytest.mark.asyncio
async def test_list_documents_raises_when_kb_missing() -> None:
    kb_id = uuid.uuid4()
    db = AsyncMock()
    service = KnowledgeBaseAccessService(db)
    service._kb_service.get_or_raise = AsyncMock(
        side_effect=KnowledgeBaseServiceError("База знаний не найдена")
    )

    with pytest.raises(KnowledgeBaseAccessServiceError) as exc:
        await service.list_documents(str(kb_id))

    assert exc.value.code == "knowledge_base_not_found"


@pytest.mark.asyncio
async def test_get_document_metadata_not_found() -> None:
    db = AsyncMock()
    db.get = AsyncMock(return_value=None)
    service = KnowledgeBaseAccessService(db)

    with pytest.raises(KnowledgeBaseAccessServiceError) as exc:
        await service.get_document_metadata(str(uuid.uuid4()))

    assert exc.value.code == "document_not_found"


@pytest.mark.asyncio
async def test_get_document_chunks_not_found() -> None:
    document = _make_document()
    version = DocumentVersion(id=uuid.uuid4(), document_id=document.id, version_number=1)

    db = AsyncMock()
    db.get = AsyncMock(side_effect=lambda model, obj_id: document if model is Document else version)
    service = KnowledgeBaseAccessService(db)
    service._find_kb_source_for_document = AsyncMock(return_value=None)
    service._resolve_document_version = AsyncMock(return_value=version)

    execute_result = MagicMock()
    execute_result.scalars.return_value.all.return_value = []
    db.execute = AsyncMock(return_value=execute_result)

    with pytest.raises(KnowledgeBaseAccessServiceError) as exc:
        await service.get_document_chunks(str(document.id))

    assert exc.value.code == "chunks_not_found"


@pytest.mark.asyncio
async def test_get_document_text_from_extracted_storage() -> None:
    document = _make_document()
    version = DocumentVersion(
        id=uuid.uuid4(),
        document_id=document.id,
        version_number=1,
        extracted_text_object_name="documents/extracted.json",
    )
    service = KnowledgeBaseAccessService(AsyncMock())
    service._get_document_or_raise = AsyncMock(return_value=document)
    service._resolve_document_version = AsyncMock(return_value=version)

    payload = '{"text": "Полный текст документа"}'.encode("utf-8")
    with patch(
        "app.agents.nd_control_agent.knowledge_base_access_service.object_storage.get_object",
        return_value=payload,
    ):
        result = await service.get_document_text(str(document.id))

    assert result.status == "ok"
    assert result.source == "extracted_text"
    assert result.text == "Полный текст документа"


@pytest.mark.asyncio
async def test_get_document_text_from_chunks_when_no_extracted_text() -> None:
    document = _make_document()
    version = DocumentVersion(id=uuid.uuid4(), document_id=document.id, version_number=1)
    service = KnowledgeBaseAccessService(AsyncMock())
    service._get_document_or_raise = AsyncMock(return_value=document)
    service._resolve_document_version = AsyncMock(return_value=version)
    service._load_extracted_text = MagicMock(return_value="")
    service._assemble_text_from_chunks = AsyncMock(return_value="Чанк 1\n\nЧанк 2")

    result = await service.get_document_text(str(document.id))

    assert result.status == "ok"
    assert result.source == "chunks"
    assert result.text == "Чанк 1\n\nЧанк 2"


@pytest.mark.asyncio
async def test_get_document_text_empty() -> None:
    document = _make_document()
    version = DocumentVersion(id=uuid.uuid4(), document_id=document.id, version_number=1)
    service = KnowledgeBaseAccessService(AsyncMock())
    service._get_document_or_raise = AsyncMock(return_value=document)
    service._resolve_document_version = AsyncMock(return_value=version)
    service._load_extracted_text = MagicMock(return_value="")
    service._assemble_text_from_chunks = AsyncMock(return_value="")

    result = await service.get_document_text(str(document.id))

    assert result.status == "empty"
    assert result.text == ""
    assert result.message


@pytest.mark.asyncio
async def test_search_in_knowledge_base_returns_fragments() -> None:
    kb_id = uuid.uuid4()
    user = MagicMock()
    hit = KnowledgeBaseSearchHit(
        content="Фрагмент текста",
        score=0.88,
        accessible=True,
        access_reason="allowed",
        knowledge_base_id=kb_id,
        knowledge_base_chunk_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        document_title="СТО-001",
        page_number=3,
        section_title="Раздел 1",
        metadata={"clause_number": "1.2"},
    )
    service = KnowledgeBaseAccessService(AsyncMock())
    service._kb_service.get_or_raise = AsyncMock(return_value=KnowledgeBase(id=kb_id, name="KB", qdrant_collection="kb_x"))
    service._search_service.search = AsyncMock(
        return_value=KnowledgeBaseTestSearchResponse(hits=[hit], answer_preview=None)
    )

    result = await service.search_in_knowledge_base(str(kb_id), "совещание", user=user)

    assert result.status == "ok"
    assert len(result.fragments) == 1
    assert result.fragments[0].text == "Фрагмент текста"
    assert result.fragments[0].page_number == 3


@pytest.mark.asyncio
async def test_search_in_knowledge_base_empty_result() -> None:
    kb_id = uuid.uuid4()
    service = KnowledgeBaseAccessService(AsyncMock())
    service._kb_service.get_or_raise = AsyncMock(return_value=KnowledgeBase(id=kb_id, name="KB", qdrant_collection="kb_x"))
    service._search_service.search = AsyncMock(
        return_value=KnowledgeBaseTestSearchResponse(hits=[], answer_preview=None)
    )

    result = await service.search_in_knowledge_base(str(kb_id), "нет данных", user=MagicMock())

    assert result.status == "empty"
    assert result.fragments == []
    assert result.message


@pytest.mark.asyncio
async def test_get_document_chunks_returns_ordered_items() -> None:
    document = _make_document()
    version_id = uuid.uuid4()
    chunk = DocumentChunk(
        id=uuid.uuid4(),
        document_id=document.id,
        document_version_id=version_id,
        chunk_index=0,
        content="Текст чанка",
        page_number=2,
        section_title="1.1",
        metadata_={"kind": "paragraph"},
    )

    db = AsyncMock()
    db.get = AsyncMock(return_value=document)
    service = KnowledgeBaseAccessService(db)
    service._resolve_document_version = AsyncMock(return_value=DocumentVersion(id=version_id, document_id=document.id))

    execute_result = MagicMock()
    execute_result.scalars.return_value.all.return_value = [chunk]
    db.execute = AsyncMock(return_value=execute_result)

    items = await service.get_document_chunks(str(document.id))

    assert len(items) == 1
    assert items[0].text == "Текст чанка"
    assert items[0].page_number == 2
    assert items[0].section == "1.1"
    assert items[0].metadata["kind"] == "paragraph"
