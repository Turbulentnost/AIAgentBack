from __future__ import annotations

import hashlib
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import SessionLocal
from app.integration.job_service import IntegrationJobService
from app.services.history_service import HistoryService
from app.services.marking_check_cache import MarkingCheckCacheService


def compute_uploads_checksum(uploads: list[tuple[str, bytes]]) -> str:
    return hashlib.sha256(b"".join(data for _, data in uploads)).hexdigest()


def classify_counts(payload: dict[str, Any]) -> tuple[int, int, int]:
    critical = int(payload.get("total_errors") or 0)
    warnings = int(payload.get("total_warnings") or 0)
    major = min(warnings, max(0, warnings // 2))
    minor = max(0, warnings - major)
    return critical, major, minor


def map_result_status(payload: dict[str, Any]) -> str:
    critical, major, minor = classify_counts(payload)
    if critical:
        return "rejected"
    if major or minor:
        return "completed_with_remarks"
    return "approved"


class CheckExecutor:
    """Runs check using cache layers without requiring live model when possible."""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._jobs = IntegrationJobService(db)

    async def run_cached(
        self,
        *,
        job_id: uuid.UUID,
        uploads: list[tuple[str, bytes]],
        designation: str | None,
    ) -> dict[str, Any]:
        job = await self._jobs.get(job_id)
        if not job:
            raise ValueError("Job not found")

        await self._jobs.set_status(job, "running")

        async with SessionLocal() as cache_db:
            cached = await MarkingCheckCacheService(cache_db).try_build_cached(
                uploads=uploads,
                designation=designation,
            )

        if cached:
            critical, major, minor = classify_counts(cached)
            await self._jobs.complete(
                job,
                result_payload=cached,
                result_status=map_result_status(cached),
                critical=critical,
                major=major,
                minor=minor,
            )
            return cached

        raise RuntimeError("Нет сохранённого результата проверки")

    async def persist_ai_result(
        self,
        *,
        job_id: uuid.UUID,
        payload: dict[str, Any],
        uploads: list[tuple[str, bytes]],
        check_params: dict | None = None,
    ) -> dict[str, Any]:
        job = await self._jobs.get(job_id)
        if not job:
            raise ValueError("Job not found")

        run = await HistoryService(self._db).save_check_run(
            payload=payload,
            uploads=uploads,
            check_params=check_params,
        )
        critical, major, minor = classify_counts(payload)
        await self._jobs.complete(
            job,
            result_payload=payload,
            result_status=map_result_status(payload),
            critical=critical,
            major=major,
            minor=minor,
            check_run_id=run.id if run else None,
        )
        return payload

    async def lookup_by_checksum(self, checksum: str) -> dict[str, Any] | None:
        run = await HistoryService(self._db).find_latest_by_checksum(checksum)
        if run and run.raw_result:
            return dict(run.raw_result)
        return None
