from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integration.document_service import DocumentService
from app.integration.exchange_log_service import ExchangeLogService
from app.integration.job_service import IntegrationJobService
from app.models.integration import IntegrationDocument, IntegrationJob
from app.schemas.integration import ErpReadinessResponse, ErpReadinessUpdate


class ErpAdapter:
    SOURCE = "1c"

    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._documents = DocumentService(db)
        self._jobs = IntegrationJobService(db)
        self._log = ExchangeLogService(db)

    async def upsert_context(self, payload: ErpReadinessUpdate) -> IntegrationDocument:
        from app.schemas.integration import UnifiedDocumentCard

        card = UnifiedDocumentCard(
            document_id=payload.document_id,
            source_system=payload.source_system or self.SOURCE,
            metadata_extra={
                "nomenclature_code": payload.nomenclature_code,
                "order_number": payload.order_number,
                "project": payload.project,
                "department": payload.department,
                "due_date": payload.due_date,
                "pdm_link": payload.pdm_link,
            },
        )
        doc = await self._documents.upsert(card)
        await self._log.log(
            sender=self.SOURCE,
            receiver="eskd-control",
            operation="ErpContextUpdate",
            result="ok",
            external_document_id=payload.document_id,
        )
        return doc

    async def get_readiness(
        self,
        *,
        document_id: str,
        source_system: str = SOURCE,
    ) -> ErpReadinessResponse:
        doc = await self._documents.get_by_external(
            source_system=source_system,
            external_document_id=document_id,
        )
        job = await self._latest_job(doc.id if doc else None)
        production_ready = bool(
            job
            and not job.is_stale
            and job.result_status == "approved"
            and job.critical_count == 0
            and job.status in {"completed", "completed_with_remarks"}
        )
        readiness_status = "unknown"
        if job:
            if job.is_stale:
                readiness_status = "stale"
            elif job.blocks_workflow:
                readiness_status = "blocked"
            elif production_ready:
                readiness_status = "production_ready"
            elif job.status == "error":
                readiness_status = "error"
            else:
                readiness_status = job.result_status or job.status

        return ErpReadinessResponse(
            document_id=document_id,
            check_id=job.id if job else None,
            production_ready=production_ready,
            readiness_status=readiness_status,
            critical_count=job.critical_count if job else 0,
            report_url=f"/api/v1/checks/{job.id}/report" if job else None,
            assignee=doc.author if doc else None,
            rework_deadline=(doc.metadata_extra or {}).get("due_date") if doc else None,
        )

    async def _latest_job(self, document_id) -> IntegrationJob | None:
        if not document_id:
            return None
        return await self._db.scalar(
            select(IntegrationJob)
            .where(IntegrationJob.document_id == document_id, IntegrationJob.is_stale.is_(False))
            .order_by(IntegrationJob.created_at.desc())
        )
