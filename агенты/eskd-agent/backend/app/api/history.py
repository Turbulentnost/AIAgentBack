from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.schemas.history import (
    CheckRunChangeRead,
    CheckRunDetailRead,
    CheckRunListItem,
    CheckRunListResponse,
    CheckRunVersionRead,
    GostSummaryRead,
)
from app.services.check_version_service import CheckVersionService
from app.services.history_service import HistoryService

router = APIRouter(prefix="/api/v1/eskd/history", tags=["eskd-history"])


def _list_item(row) -> CheckRunListItem:
    raw = row.raw_result or {}
    gost_summary = GostSummaryRead(**row.gost_summary) if row.gost_summary else None
    progress = raw.get("progress_percent")
    if progress is None and row.pages_count:
        processed = int(raw.get("processed") or 0)
        total = int(raw.get("total_items") or row.pages_count or 0)
        progress = round(100 * processed / total, 1) if total else None
    return CheckRunListItem(
        id=row.id,
        job_id=row.job_id,
        created_at=row.created_at,
        original_filename=row.original_filename,
        designation=row.designation,
        status=row.status,
        total_errors=row.total_errors,
        total_warnings=row.total_warnings,
        pages_count=row.pages_count,
        version_no=row.version_no or 1,
        created_by_login=row.created_by_login,
        created_by_name=row.created_by_name,
        verified_by_login=row.verified_by_login,
        verified_by_name=row.verified_by_name,
        human_verified_at=row.human_verified_at,
        gost_summary=gost_summary,
        progress_percent=float(progress) if progress is not None else None,
        processed_pages=int(raw.get("processed") or 0) if row.status == "running" else None,
    )


@router.get("", response_model=CheckRunListResponse)
async def list_history(
    db: AsyncSession = Depends(get_db),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    filename: str | None = None,
    designation: str | None = None,
):
    rows, total = await HistoryService(db).list_runs(
        page=page,
        size=size,
        filename=filename,
        designation=designation,
    )
    return CheckRunListResponse(
        items=[_list_item(row) for row in rows],
        total=total,
        page=page,
        size=size,
    )


@router.get("/{run_id}", response_model=CheckRunDetailRead)
async def get_history_item(run_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    row = await HistoryService(db).get_run(run_id)
    if not row:
        raise HTTPException(404, "Проверка не найдена")

    base = _list_item(row)
    return CheckRunDetailRead(
        **base.model_dump(),
        content_type=row.content_type,
        file_size_bytes=row.file_size_bytes,
        file_sha256=row.file_sha256,
        check_params=row.check_params,
        model=row.model,
        adapter=row.adapter,
        raw_result=row.raw_result,
        document_key=row.document_key,
        parent_run_id=row.parent_run_id,
    )


@router.get("/{run_id}/versions", response_model=list[CheckRunVersionRead])
async def list_run_versions(run_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    rows = await CheckVersionService(db).list_versions(run_id)
    if not rows:
        raise HTTPException(404, "Проверка не найдена")
    return [
        CheckRunVersionRead(
            id=row.id,
            version_no=row.version_no or 1,
            created_at=row.created_at,
            created_by_login=row.created_by_login,
            created_by_name=row.created_by_name,
            total_errors=row.total_errors,
            total_warnings=row.total_warnings,
            status=row.status,
            human_verified_at=row.human_verified_at,
            verified_by_name=row.verified_by_name,
        )
        for row in rows
    ]


@router.get("/{run_id}/changes", response_model=list[CheckRunChangeRead])
async def list_run_changes(run_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    if not await HistoryService(db).get_run(run_id):
        raise HTTPException(404, "Проверка не найдена")
    rows = await CheckVersionService(db).list_changes(run_id)
    return [
        CheckRunChangeRead(
            id=row.id,
            run_id=row.run_id,
            parent_run_id=row.parent_run_id,
            version_no=row.version_no,
            change_type=row.change_type,
            summary=row.summary,
            changed_by_login=row.changed_by_login,
            changed_by_name=row.changed_by_name,
            created_at=row.created_at,
            diff=row.diff,
        )
        for row in rows
    ]
