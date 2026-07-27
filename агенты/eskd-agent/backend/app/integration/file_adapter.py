from __future__ import annotations

import json
import shutil
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.integration.check_executor import CheckExecutor, compute_uploads_checksum
from app.integration.document_service import DocumentService
from app.integration.exchange_log_service import ExchangeLogService
from app.integration.job_service import IntegrationJobService
from app.integration.webhook_service import WebhookService
from app.schemas.integration import UnifiedDocumentCard


class FileExchangeAdapter:
    METADATA_NAMES = ("metadata.json", "metadata.xml")

    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._jobs = IntegrationJobService(db)
        self._documents = DocumentService(db)
        self._executor = CheckExecutor(db)
        self._log = ExchangeLogService(db)
        self._webhooks = WebhookService(db)
        self._root = Path(settings.integration_root)
        self.incoming = self._root / settings.integration_incoming_dir
        self.processing = self._root / settings.integration_processing_dir
        self.completed = self._root / settings.integration_completed_dir
        self.error = self._root / settings.integration_error_dir
        self.archive = self._root / settings.integration_archive_dir

    def ensure_dirs(self) -> None:
        for path in (self.incoming, self.processing, self.completed, self.error, self.archive):
            path.mkdir(parents=True, exist_ok=True)

    async def scan_incoming(self) -> int:
        self.ensure_dirs()
        processed = 0
        for package_dir in sorted(self.incoming.iterdir()):
            if not package_dir.is_dir():
                continue
            target = self.processing / package_dir.name
            if target.exists():
                continue
            shutil.move(str(package_dir), str(target))
            try:
                await self._process_package(target)
                processed += 1
            except Exception as exc:
                err_dir = self.error / package_dir.name
                if target.exists():
                    shutil.move(str(target), str(err_dir))
                await self._log.log(
                    sender="pdm-file",
                    receiver="eskd-control",
                    operation="FileImport",
                    result="error",
                    error_message=str(exc),
                )
        return processed

    async def _process_package(self, package_dir: Path) -> None:
        metadata_path = self._find_metadata(package_dir)
        if metadata_path is None:
            raise ValueError(f"metadata.json/xml not found in {package_dir.name}")

        card = self._parse_metadata(metadata_path)
        uploads = self._collect_uploads(package_dir, card)
        if not uploads:
            raise ValueError("No document files in package")

        checksum = compute_uploads_checksum(uploads)
        card.checksum = card.checksum or checksum
        request_id = f"file:{package_dir.name}:{checksum[:16]}"

        job, created = await self._jobs.create_or_get(
            request_id=request_id,
            card=card,
            submitted_by="file-adapter",
        )
        if not created and job.status in {"completed", "completed_with_remarks", "approved"}:
            self._finalize_package(package_dir, success=True)
            return

        await self._jobs.set_status(job, "queued")
        try:
            payload = await self._executor.run_cached(
                job_id=job.id,
                uploads=uploads,
                designation=card.designation,
            )
        except RuntimeError:
            await self._jobs.set_status(job, "accepted")
            raise

        summary = (await self._jobs.to_summary(job)).model_dump()
        await self._webhooks.enqueue_for_job(job, summary)
        await self._log.log(
            sender=card.source_system,
            receiver="eskd-control",
            operation="FileImport",
            result="ok",
            request_id=request_id,
            job_id=job.id,
            external_document_id=card.document_id,
            designation=card.designation,
            revision=card.revision,
            payload_summary={"source": payload.get("status"), "package": package_dir.name},
        )
        self._finalize_package(package_dir, success=True)

    def _finalize_package(self, package_dir: Path, *, success: bool) -> None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        dest_root = self.completed if success else self.error
        dest = dest_root / f"{package_dir.name}_{stamp}"
        shutil.move(str(package_dir), str(dest))
        archive_dest = self.archive / dest.name
        shutil.copytree(dest, archive_dest)

    def _find_metadata(self, package_dir: Path) -> Path | None:
        for name in self.METADATA_NAMES:
            path = package_dir / name
            if path.is_file():
                return path
        return None

    def _parse_metadata(self, path: Path) -> UnifiedDocumentCard:
        if path.suffix.lower() == ".json":
            data = json.loads(path.read_text(encoding="utf-8"))
            return UnifiedDocumentCard.model_validate(data)
        root = ET.fromstring(path.read_text(encoding="utf-8"))
        data = {child.tag: (child.text or "").strip() for child in root}
        related = []
        for rel in root.findall(".//related_document"):
            related.append(
                {
                    "document_id": rel.findtext("document_id"),
                    "relation": rel.findtext("relation"),
                }
            )
        return UnifiedDocumentCard(
            document_id=data.get("document_id") or data.get("id") or path.parent.name,
            source_system=data.get("source_system") or "pdm-file",
            designation=data.get("designation"),
            document_type=data.get("document_type"),
            name=data.get("name"),
            revision=data.get("revision"),
            sheet_count=int(data["sheet_count"]) if data.get("sheet_count") else None,
            author=data.get("author"),
            department=data.get("department"),
            product_id=data.get("product_id"),
            checksum=data.get("checksum"),
            related_documents=related,
            status=data.get("status"),
        )

    def _collect_uploads(
        self,
        package_dir: Path,
        card: UnifiedDocumentCard,
    ) -> list[tuple[str, bytes]]:
        uploads: list[tuple[str, bytes]] = []
        declared = {f.get("filename") for f in (card.files or []) if f.get("filename")}
        for path in sorted(package_dir.iterdir()):
            if not path.is_file() or path.name in self.METADATA_NAMES:
                continue
            if declared and path.name not in declared:
                continue
            uploads.append((path.name, path.read_bytes()))
        return uploads
