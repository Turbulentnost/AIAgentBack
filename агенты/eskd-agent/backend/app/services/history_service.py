from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.gost.aggregation import aggregate_from_check_response
from app.gost.catalog import GOST_LINE_KEYS
from app.models.check_run import EskdCheckRun
from app.services.check_upload_storage import CheckUploadStorage
from app.services.check_version_service import CheckVersionService
from app.services.user_service import EskdActor

_log = logging.getLogger("eskd.history")


class HistoryService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def find_by_job_id(self, job_id: str) -> EskdCheckRun | None:
        jid = job_id.strip()
        if not jid:
            return None
        rows = await self._db.scalars(
            select(EskdCheckRun)
            .where(EskdCheckRun.job_id == jid)
            .order_by(EskdCheckRun.created_at.desc())
            .limit(1)
        )
        return rows.first()

    async def create_running_run(
        self,
        *,
        job_id: str,
        uploads: list[tuple[str, bytes]],
        check_params: dict[str, Any] | None = None,
        actor: EskdActor | None = None,
        total_items: int = 0,
    ) -> EskdCheckRun:
        import hashlib

        primary_name = uploads[0][0] if uploads else None
        total_size = sum(len(data) for _, data in uploads)
        sha = hashlib.sha256(b"".join(data for _, data in uploads)).hexdigest() if uploads else None
        initial_payload: dict[str, Any] = {
            "job_id": job_id,
            "status": "running",
            "total_items": total_items,
            "processed": 0,
            "failed": 0,
            "total_errors": 0,
            "total_warnings": 0,
            "progress_percent": 0.0,
            "items": [],
        }
        gost_summary = {"passed": list(GOST_LINE_KEYS), "warnings": {}, "errors": {}}
        run = EskdCheckRun(
            job_id=job_id,
            original_filename=primary_name,
            designation=None,
            content_type=None,
            file_size_bytes=total_size or None,
            file_sha256=sha,
            pages_count=total_items or None,
            check_params=check_params,
            model=None,
            adapter=None,
            status="running",
            total_errors=0,
            total_warnings=0,
            raw_result=initial_payload,
            gost_summary=gost_summary,
        )
        self._db.add(run)
        await self._db.flush()
        await CheckVersionService(self._db).apply_version_metadata(run, actor=actor)
        await self._db.commit()
        await self._db.refresh(run)
        persist_check_uploads(uploads)
        return run

    async def update_run_progress(self, run_id: uuid.UUID, payload: dict[str, Any]) -> None:
        run = await self._db.get(EskdCheckRun, run_id)
        if not run:
            return
        items = payload.get("items") or []
        run.status = str(payload.get("status") or run.status)
        run.total_errors = int(payload.get("total_errors") or 0)
        run.total_warnings = int(payload.get("total_warnings") or 0)
        run.pages_count = int(payload.get("total_items") or len(items) or run.pages_count or 0)
        if payload.get("designation"):
            run.designation = payload.get("designation")
        if payload.get("model"):
            run.model = payload.get("model")
        if payload.get("adapter"):
            run.adapter = payload.get("adapter")
        run.raw_result = payload
        run.gost_summary = payload.get("gost_summary") or aggregate_from_check_response(payload)
        await self._db.commit()

    async def finalize_run(
        self,
        run_id: uuid.UUID,
        *,
        payload: dict[str, Any],
        uploads: list[tuple[str, bytes]],
        check_params: dict[str, Any] | None = None,
        actor: EskdActor | None = None,
    ) -> EskdCheckRun | None:
        run = await self._db.get(EskdCheckRun, run_id)
        if not run:
            return await self.save_check_run(
                payload=payload,
                uploads=uploads,
                check_params=check_params,
                actor=actor,
            )
        items = payload.get("items") or []
        run.job_id = str(payload.get("job_id") or run.job_id)
        run.designation = payload.get("designation")
        run.model = payload.get("model")
        run.adapter = payload.get("adapter")
        run.status = str(payload.get("status") or "completed")
        run.total_errors = int(payload.get("total_errors") or 0)
        run.total_warnings = int(payload.get("total_warnings") or 0)
        run.pages_count = len(items) or run.pages_count
        run.check_params = check_params or run.check_params
        run.raw_result = payload
        run.gost_summary = aggregate_from_check_response(payload)
        if actor and not run.created_by_login:
            run.created_by_user_id = actor.user_id
            run.created_by_login = actor.login
            run.created_by_name = actor.display_name
        await self._db.commit()
        await self._db.refresh(run)
        persist_check_uploads(uploads)
        return run

    async def save_check_run(
        self,
        *,
        payload: dict[str, Any],
        uploads: list[tuple[str, bytes]],
        check_params: dict[str, Any] | None = None,
        actor: EskdActor | None = None,
    ) -> EskdCheckRun | None:
        if not payload.get("items") and payload.get("status") == "text_only":
            return None

        import hashlib

        primary_name = uploads[0][0] if uploads else None
        total_size = sum(len(data) for _, data in uploads)
        sha = hashlib.sha256(b"".join(data for _, data in uploads)).hexdigest() if uploads else None
        pages_count = len(payload.get("items") or [])

        run = EskdCheckRun(
            job_id=str(payload.get("job_id") or uuid.uuid4()),
            original_filename=primary_name,
            designation=payload.get("designation"),
            content_type=None,
            file_size_bytes=total_size or None,
            file_sha256=sha,
            pages_count=pages_count or None,
            check_params=check_params,
            model=payload.get("model"),
            adapter=payload.get("adapter"),
            status=str(payload.get("status") or "completed"),
            total_errors=int(payload.get("total_errors") or 0),
            total_warnings=int(payload.get("total_warnings") or 0),
            raw_result=payload,
            gost_summary=aggregate_from_check_response(payload),
        )
        self._db.add(run)
        await self._db.flush()

        change = await CheckVersionService(self._db).apply_version_metadata(run, actor=actor)
        await self._db.commit()
        await self._db.refresh(run)
        await self._db.refresh(change)
        persist_check_uploads(uploads)
        return run

    async def list_runs(
        self,
        *,
        page: int = 1,
        size: int = 20,
        filename: str | None = None,
        designation: str | None = None,
    ) -> tuple[list[EskdCheckRun], int]:
        query = select(EskdCheckRun).order_by(EskdCheckRun.created_at.desc())
        count_query = select(func.count()).select_from(EskdCheckRun)

        if filename:
            query = query.where(EskdCheckRun.original_filename.ilike(f"%{filename}%"))
            count_query = count_query.where(EskdCheckRun.original_filename.ilike(f"%{filename}%"))
        if designation:
            query = query.where(EskdCheckRun.designation.ilike(f"%{designation}%"))
            count_query = count_query.where(EskdCheckRun.designation.ilike(f"%{designation}%"))

        total = int((await self._db.scalar(count_query)) or 0)
        offset = (page - 1) * size
        rows = (await self._db.scalars(query.offset(offset).limit(size))).all()
        return list(rows), total

    async def get_run(self, run_id: uuid.UUID) -> EskdCheckRun | None:
        return await self._db.get(EskdCheckRun, run_id)

    async def find_latest_by_filename(self, filename: str) -> EskdCheckRun | None:
        name = filename.strip().lower()
        if not name:
            return None
        runs = (
            await self._db.scalars(
                select(EskdCheckRun)
                .where(func.lower(EskdCheckRun.original_filename) == name)
                .order_by(EskdCheckRun.created_at.desc())
            )
        ).all()
        for run in runs:
            raw = run.raw_result or {}
            if raw.get("items") or raw.get("status") not in {None, "text_only"}:
                return run
        return None

    async def find_latest_by_checksum(self, checksum: str) -> EskdCheckRun | None:
        sha = checksum.strip().lower()
        if not sha:
            return None
        runs = (
            await self._db.scalars(
                select(EskdCheckRun)
                .where(func.lower(EskdCheckRun.file_sha256) == sha)
                .order_by(EskdCheckRun.created_at.desc())
            )
        ).all()
        for run in runs:
            raw = run.raw_result or {}
            if raw.get("items") or raw.get("status") not in {None, "text_only"}:
                return run
        return None


def persist_check_uploads(uploads: list[tuple[str, bytes]]) -> None:
    if not uploads:
        return
    import hashlib

    sha = hashlib.sha256(b"".join(data for _, data in uploads)).hexdigest()
    primary_name = uploads[0][0]
    try:
        CheckUploadStorage().save(sha256=sha, filename=primary_name, data=uploads[0][1])
    except ValueError:
        return
    except OSError as exc:
        _log.warning("failed to persist check upload: %s", exc)


async def persist_check_run_safe(
    db: AsyncSession,
    *,
    payload: dict[str, Any],
    uploads: list[tuple[str, bytes]],
    check_params: dict[str, Any] | None = None,
    actor: EskdActor | None = None,
    existing_run_id: uuid.UUID | None = None,
) -> uuid.UUID | None:
    try:
        if payload.get("status") not in {"from_marking", "from_cache"}:
            persist_check_uploads(uploads)
        service = HistoryService(db)
        if existing_run_id:
            run = await service.finalize_run(
                existing_run_id,
                payload=payload,
                uploads=uploads,
                check_params=check_params,
                actor=actor,
            )
        else:
            job_id = str(payload.get("job_id") or "")
            existing = await service.find_by_job_id(job_id) if job_id else None
            if existing and existing.status == "running":
                run = await service.finalize_run(
                    existing.id,
                    payload=payload,
                    uploads=uploads,
                    check_params=check_params,
                    actor=actor,
                )
            else:
                run = await service.save_check_run(
                    payload=payload,
                    uploads=uploads,
                    check_params=check_params,
                    actor=actor,
                )
        return run.id if run else None
    except Exception as exc:
        _log.warning("failed to save check run: %s", exc)
        return None
