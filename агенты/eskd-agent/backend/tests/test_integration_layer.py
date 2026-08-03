"""Unit tests for integration job idempotency and checksum invalidation."""

from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock

from app.integration.document_service import DocumentService
from app.integration.job_service import IntegrationJobService
from app.models.integration import IntegrationDocument, IntegrationJob
from app.schemas.integration import UnifiedDocumentCard


def test_create_or_get_idempotent() -> None:
    db = MagicMock()
    existing_job = IntegrationJob(
        id=uuid.uuid4(),
        request_id="req-1",
        source_system="pdm",
        status="accepted",
    )
    jobs = IntegrationJobService(db)
    jobs.get_by_request_id = AsyncMock(return_value=existing_job)
    jobs._documents = MagicMock()
    jobs._documents.upsert = AsyncMock()
    jobs._log.log = AsyncMock()

    card = UnifiedDocumentCard(document_id="DOC-1", source_system="pdm")

    async def _run():
        job, created = await jobs.create_or_get(request_id="req-1", card=card)
        assert created is False
        assert job.request_id == "req-1"
        jobs._documents.upsert.assert_not_called()

    asyncio.run(_run())


def test_document_upsert_invalidates_on_checksum_change() -> None:
    db = MagicMock()
    doc = IntegrationDocument(
        id=uuid.uuid4(),
        external_document_id="DOC-1",
        source_system="pdm",
        revision="A",
        checksum="aaa",
    )
    svc = DocumentService(db)
    svc._db.scalar = AsyncMock(return_value=doc)
    svc._db.commit = AsyncMock()
    svc._db.refresh = AsyncMock()
    svc._invalidate_jobs_for_document = AsyncMock()

    card = UnifiedDocumentCard(
        document_id="DOC-1",
        source_system="pdm",
        revision="A",
        checksum="bbb",
    )

    async def _run():
        updated = await svc.upsert(card)
        assert updated.checksum == "bbb"
        svc._invalidate_jobs_for_document.assert_awaited_once_with(doc.id)

    asyncio.run(_run())


def test_classify_counts() -> None:
    from app.integration.check_executor import classify_counts, map_result_status

    critical, major, minor = classify_counts({"total_errors": 2, "total_warnings": 3})
    assert critical == 2
    assert major + minor == 3
    assert map_result_status({"total_errors": 1}) == "rejected"
    assert map_result_status({"total_errors": 0, "total_warnings": 0}) == "approved"
