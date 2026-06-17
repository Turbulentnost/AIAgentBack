from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.enums import NdExtractionStatus, NdGraphEntityType, NdRelationType
from app.models.nd_control_structural import DocumentCard, NdRelation, ProcessCard
from app.services.nd_control_department_detail_service import NdControlDepartmentDetailService


def _dept(kb_ids: list[uuid.UUID] | None = None):
    dept = MagicMock()
    dept.id = uuid.uuid4()
    dept.name = "Отдел ИТ"
    dept.knowledge_base_links = [MagicMock(knowledge_base_id=kb_id) for kb_id in (kb_ids or [uuid.uuid4()])]
    return dept


@pytest.mark.asyncio
async def test_list_document_cards_returns_structural_cards() -> None:
    db = AsyncMock()
    service = NdControlDepartmentDetailService(db)
    kb_id = uuid.uuid4()
    dept = _dept([kb_id])
    card = DocumentCard(
        id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        knowledge_base_id=kb_id,
        document_code="И-15-230",
        title="Тест",
        extraction_status=NdExtractionStatus.COMPLETED,
    )

    service.department_service.get_department_or_raise = AsyncMock(return_value=dept)
    service.get_department_scope = AsyncMock(
        return_value={"department": dept, "kb_ids": [kb_id], "document_ids": [card.document_id], "process_ids": []}
    )
    db.scalar = AsyncMock(return_value=1)
    db.execute = AsyncMock(
        return_value=MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[card]))))
    )
    service._document_card_item = AsyncMock(return_value={"document_card_id": card.id, "title": card.title})

    items, total = await service.list_document_cards(dept.id)

    assert total == 1
    assert len(items) == 1


@pytest.mark.asyncio
async def test_department_relation_filter_includes_department_and_documents() -> None:
    service = NdControlDepartmentDetailService(AsyncMock())
    dept = _dept()
    doc_id = uuid.uuid4()
    scope = {
        "department": dept,
        "kb_ids": [uuid.uuid4()],
        "document_ids": [doc_id],
        "process_ids": [],
    }
    clause = service._department_relation_filter(scope)
    assert clause is not None
