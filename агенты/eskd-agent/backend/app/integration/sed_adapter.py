from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.integration.exchange_log_service import ExchangeLogService
from app.integration.job_service import IntegrationJobService
from app.integration.report_service import ReportService
from app.schemas.integration import SedArchivePayload, SedArchiveResponse


class SedArchiveAdapter:
    SOURCE = "sed"

    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._jobs = IntegrationJobService(db)
        self._log = ExchangeLogService(db)
        self._archive_root = Path(settings.integration_root) / settings.sed_archive_dir

    async def archive(self, payload: SedArchivePayload) -> SedArchiveResponse:
        job = await self._jobs.get(payload.check_id)
        if not job:
            raise ValueError("Check job not found")
        summary = (await self._jobs.to_summary(job)).model_dump()
        json_report = ReportService.build_json_report(job, summary)
        pdf_bytes = ReportService.build_pdf_bytes(job, summary)
        checksum = hashlib.sha256(json.dumps(json_report, sort_keys=True).encode()).hexdigest()

        self._archive_root.mkdir(parents=True, exist_ok=True)
        ref = f"{payload.source_system}_{payload.document_id}_{payload.revision or 'rev0'}_{job.id}"
        base = self._archive_root / ref
        base.mkdir(parents=True, exist_ok=True)
        (base / "protocol.json").write_text(
            json.dumps(json_report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (base / "protocol.pdf").write_bytes(pdf_bytes)
        manifest = {
            "archive_ref": ref,
            "document_id": payload.document_id,
            "source_system": payload.source_system,
            "revision": payload.revision,
            "check_id": str(job.id),
            "checksum": checksum,
            "ruleset_version": job.ruleset_version,
            "decision": payload.decision,
            "signature_info": payload.signature_info,
            "archived_at": datetime.now(timezone.utc).isoformat(),
        }
        (base / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        await self._log.log(
            sender="eskd-control",
            receiver=self.SOURCE,
            operation="ArchiveProtocol",
            result="ok",
            job_id=job.id,
            external_document_id=payload.document_id,
            revision=payload.revision,
            payload_summary={"archive_ref": ref},
        )
        return SedArchiveResponse(
            archived=True,
            archive_ref=ref,
            checksum=checksum,
            ruleset_version=job.ruleset_version,
        )
