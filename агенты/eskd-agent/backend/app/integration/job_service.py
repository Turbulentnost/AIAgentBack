from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.integration.document_service import DocumentService
from app.integration.exchange_log_service import ExchangeLogService
from app.models.integration import IntegrationDocument, IntegrationJob
from app.schemas.integration import CheckSummaryResponse, UnifiedDocumentCard


class IntegrationJobService:
    RULESET_VERSION = "ESKD-2026.04"

    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._documents = DocumentService(db)
        self._log = ExchangeLogService(db)

    async def get_by_request_id(self, request_id: str) -> IntegrationJob | None:
        return await self._db.scalar(
            select(IntegrationJob).where(IntegrationJob.request_id == request_id)
        )

    async def get(self, job_id: uuid.UUID) -> IntegrationJob | None:
        return await self._db.get(IntegrationJob, job_id)

    async def create_or_get(
        self,
        *,
        request_id: str,
        card: UnifiedDocumentCard,
        submitted_by: str | None = None,
        ruleset_version: str | None = None,
    ) -> tuple[IntegrationJob, bool]:
        existing = await self.get_by_request_id(request_id)
        if existing:
            await self._log.log(
                sender=card.source_system,
                receiver="eskd-control",
                operation="CreateCheck",
                result="duplicate",
                request_id=request_id,
                job_id=existing.id,
                external_document_id=card.document_id,
                designation=card.designation,
                revision=card.revision,
                actor=submitted_by,
            )
            return existing, False

        doc = await self._documents.upsert(card)
        if card.checksum:
            await self._invalidate_other_checksum_jobs(doc)

        job = IntegrationJob(
            request_id=request_id,
            document_id=doc.id,
            source_system=card.source_system,
            status="accepted",
            ruleset_version=ruleset_version or self.RULESET_VERSION,
            submitted_by=submitted_by,
        )
        self._db.add(job)
        await self._db.commit()
        await self._db.refresh(job)
        await self._log.log(
            sender=card.source_system,
            receiver="eskd-control",
            operation="CreateCheck",
            result="ok",
            request_id=request_id,
            job_id=job.id,
            external_document_id=card.document_id,
            designation=card.designation,
            revision=card.revision,
            actor=submitted_by,
        )
        return job, True

    async def _invalidate_other_checksum_jobs(self, doc: IntegrationDocument) -> None:
        if not doc.checksum:
            return
        await self._db.execute(
            update(IntegrationJob)
            .join(IntegrationDocument, IntegrationJob.document_id == IntegrationDocument.id)
            .where(
                IntegrationDocument.source_system == doc.source_system,
                IntegrationDocument.external_document_id == doc.external_document_id,
                IntegrationDocument.checksum != doc.checksum,
                IntegrationJob.is_stale.is_(False),
            )
            .values(is_stale=True)
        )
        await self._db.commit()

    async def set_status(self, job: IntegrationJob, status: str) -> IntegrationJob:
        job.status = status
        await self._db.commit()
        await self._db.refresh(job)
        return job

    async def complete(
        self,
        job: IntegrationJob,
        *,
        result_payload: dict,
        result_status: str,
        critical: int,
        major: int,
        minor: int,
        check_run_id: uuid.UUID | None = None,
    ) -> IntegrationJob:
        job.status = "completed_with_remarks" if critical or major or minor else "completed"
        job.result_status = result_status
        job.result_payload = result_payload
        job.critical_count = critical
        job.major_count = major
        job.minor_count = minor
        job.blocks_workflow = critical > 0
        job.check_run_id = check_run_id
        job.completed_at = datetime.now(timezone.utc)
        await self._db.commit()
        await self._db.refresh(job)
        return job

    async def fail(self, job: IntegrationJob, message: str) -> IntegrationJob:
        job.status = "error"
        job.error_message = message
        job.completed_at = datetime.now(timezone.utc)
        await self._db.commit()
        await self._db.refresh(job)
        return job

    async def cancel(self, job: IntegrationJob) -> IntegrationJob:
        job.status = "cancelled"
        job.completed_at = datetime.now(timezone.utc)
        await self._db.commit()
        await self._db.refresh(job)
        return job

    async def to_summary(self, job: IntegrationJob) -> CheckSummaryResponse:
        doc = await self._db.get(IntegrationDocument, job.document_id) if job.document_id else None
        payload = job.result_payload or {}
        source = payload.get("status")
        return CheckSummaryResponse(
            check_id=job.id,
            request_id=job.request_id,
            document_id=doc.external_document_id if doc else None,
            designation=doc.designation if doc else None,
            revision=doc.revision if doc else None,
            status=job.status,
            result_status=job.result_status,
            critical_count=job.critical_count,
            major_count=job.major_count,
            minor_count=job.minor_count,
            blocks_workflow=job.blocks_workflow,
            ruleset_version=job.ruleset_version,
            report_url=f"/api/v1/checks/{job.id}/report",
            report_json_url=f"/api/v1/checks/{job.id}/report?format=json",
            checked_at=job.completed_at,
            is_stale=job.is_stale,
            source=source,
        )
