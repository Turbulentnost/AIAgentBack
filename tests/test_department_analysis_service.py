from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.enums import (
    DepartmentAnalysisRunStatus,
    DepartmentAnalysisStep,
    NdExtractionStatus,
)
from app.models.nd_control_analysis import DepartmentAnalysisRun
from app.models.nd_control_structural import DocumentCard
from app.services.department_analysis_service import (
    DepartmentAnalysisService,
    calculate_progress_percent,
)


def _dept(kb_ids: list[uuid.UUID] | None = None):
    dept = MagicMock()
    dept.id = uuid.uuid4()
    dept.name = "Отдел ИТ"
    dept.knowledge_base_links = [MagicMock(knowledge_base_id=kb_id) for kb_id in (kb_ids or [uuid.uuid4()])]
    return dept


@pytest.mark.asyncio
async def test_extract_skips_completed_document_card() -> None:
    db = AsyncMock()
    service = DepartmentAnalysisService(db)
    kb_id = uuid.uuid4()
    doc_id = uuid.uuid4()
    completed = DocumentCard(
        id=uuid.uuid4(),
        document_id=doc_id,
        knowledge_base_id=kb_id,
        extraction_status=NdExtractionStatus.COMPLETED,
    )

    service.kb_access.list_documents = AsyncMock(
        return_value=[MagicMock(document_id=doc_id, file_name="doc.pdf")]
    )
    service._get_structural_card = AsyncMock(return_value=completed)
    service.extraction_service.extract_document_card = AsyncMock()

    summary = await service.extract_document_cards_for_knowledge_base(kb_id)

    assert summary["skipped"] == 1
    assert summary["processed"] == 0
    service.extraction_service.extract_document_card.assert_not_called()


@pytest.mark.asyncio
async def test_extract_reprocesses_failed_document_card() -> None:
    db = AsyncMock()
    service = DepartmentAnalysisService(db)
    kb_id = uuid.uuid4()
    doc_id = uuid.uuid4()
    failed = DocumentCard(
        id=uuid.uuid4(),
        document_id=doc_id,
        knowledge_base_id=kb_id,
        extraction_status=NdExtractionStatus.FAILED,
    )

    service.kb_access.list_documents = AsyncMock(
        return_value=[MagicMock(document_id=doc_id, file_name="doc.pdf")]
    )
    service._get_structural_card = AsyncMock(return_value=failed)
    service.extraction_service.extract_document_card = AsyncMock(
        return_value=DocumentCard(
            id=uuid.uuid4(),
            document_id=doc_id,
            knowledge_base_id=kb_id,
            extraction_status=NdExtractionStatus.COMPLETED,
        )
    )

    summary = await service.extract_document_cards_for_knowledge_base(kb_id)

    assert summary["processed"] == 1
    service.extraction_service.extract_document_card.assert_called_once()


@pytest.mark.asyncio
async def test_force_reextract_reprocesses_completed() -> None:
    db = AsyncMock()
    service = DepartmentAnalysisService(db)
    kb_id = uuid.uuid4()
    doc_id = uuid.uuid4()
    completed = DocumentCard(
        id=uuid.uuid4(),
        document_id=doc_id,
        knowledge_base_id=kb_id,
        extraction_status=NdExtractionStatus.COMPLETED,
    )

    service.kb_access.list_documents = AsyncMock(
        return_value=[MagicMock(document_id=doc_id, file_name="doc.pdf")]
    )
    service._get_structural_card = AsyncMock(return_value=completed)
    service.extraction_service.extract_document_card = AsyncMock(
        return_value=completed
    )

    summary = await service.extract_document_cards_for_knowledge_base(kb_id, force_reextract=True)

    assert summary["processed"] == 1
    service.extraction_service.extract_document_card.assert_called_once()


@pytest.mark.asyncio
async def test_document_failure_does_not_stop_kb_processing() -> None:
    db = AsyncMock()
    service = DepartmentAnalysisService(db)
    kb_id = uuid.uuid4()
    doc1, doc2 = uuid.uuid4(), uuid.uuid4()

    service.kb_access.list_documents = AsyncMock(
        return_value=[
            MagicMock(document_id=doc1, file_name="a.pdf"),
            MagicMock(document_id=doc2, file_name="b.pdf"),
        ]
    )
    service._get_structural_card = AsyncMock(return_value=None)

    async def _extract(document_id: str):
        if document_id == str(doc1):
            raise RuntimeError("LLM down")
        return DocumentCard(
            id=uuid.uuid4(),
            document_id=uuid.UUID(document_id),
            knowledge_base_id=kb_id,
            extraction_status=NdExtractionStatus.COMPLETED,
        )

    service.extraction_service.extract_document_card = AsyncMock(side_effect=_extract)

    summary = await service.extract_document_cards_for_knowledge_base(kb_id)

    assert summary["failed"] == 1
    assert summary["processed"] == 1


def test_progress_percent_for_extraction_step() -> None:
    assert calculate_progress_percent(
        DepartmentAnalysisStep.EXTRACTING_DOCUMENT_CARDS,
        processed=5,
        total=10,
    ) == 42


@pytest.mark.asyncio
async def test_execute_marks_completed_with_warnings() -> None:
    db = AsyncMock()
    service = DepartmentAnalysisService(db)
    run = DepartmentAnalysisRun(
        id=uuid.uuid4(),
        department_id=uuid.uuid4(),
        status=DepartmentAnalysisRunStatus.PENDING,
        current_step=DepartmentAnalysisStep.INITIALIZING,
    )
    dept = _dept()

    service._get_run_or_raise = AsyncMock(return_value=run)
    service.department_service.get_department_or_raise = AsyncMock(return_value=dept)
    service.count_department_documents = AsyncMock(return_value=2)
    service.extract_document_cards_for_department = AsyncMock(
        return_value={
            "total_documents": 2,
            "processed": 1,
            "skipped": 0,
            "failed": 0,
            "needs_review": 1,
            "knowledge_bases": [],
            "documents": [],
        }
    )
    service.build_department_profile_after_extraction = AsyncMock(
        return_value={"status": "needs_review", "processes_count": 1}
    )
    service._build_department_relations = AsyncMock(return_value=2)
    db.flush = AsyncMock()
    db.commit = AsyncMock()

    result = await service.execute_department_analysis(run.id)

    assert result.status == DepartmentAnalysisRunStatus.COMPLETED_WITH_WARNINGS
    assert result.progress_percent == 100


@pytest.mark.asyncio
async def test_start_department_analysis_creates_run() -> None:
    db = AsyncMock()
    service = DepartmentAnalysisService(db)
    dept = _dept()
    service.department_service.get_department_or_raise = AsyncMock(return_value=dept)
    db.flush = AsyncMock()

    run = await service.start_department_analysis(dept.id)

    assert run.department_id == dept.id
    assert run.status == DepartmentAnalysisRunStatus.PENDING
    db.add.assert_called_once()
