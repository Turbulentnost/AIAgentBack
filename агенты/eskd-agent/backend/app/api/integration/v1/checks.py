from __future__ import annotations

import json
import uuid
from typing import Any

import httpx
from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.responses import JSONResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.api.integration.deps import IntegrationPrincipal, require_permission
from app.config import settings
from app.format_processing import process_uploads
from app.integration.check_executor import CheckExecutor, compute_uploads_checksum
from app.integration.job_service import IntegrationJobService
from app.integration.report_service import ReportService
from app.integration.webhook_service import WebhookService
from app.schemas.integration import (
    CheckCreateRequest,
    CheckSummaryResponse,
    FindingsResponse,
    UnifiedDocumentCard,
)

router = APIRouter(prefix="/api/v1/checks", tags=["integration-checks"])


def _request_id(header: str | None, body: str | None) -> str:
    rid = (header or body or "").strip()
    if not rid:
        rid = str(uuid.uuid4())
    return rid[:128]


async def _raw_uploads(upload_files: list[UploadFile]) -> list[tuple[str, bytes]]:
    return [(uf.filename or "upload.bin", await uf.read()) for uf in upload_files]


async def _maybe_run_ai(
    *,
    uploads: list[tuple[str, bytes]],
    designation: str | None,
    run_ai: bool,
) -> dict[str, Any] | None:
    if not run_ai:
        return None
    try:
        model_files, extracted, warnings = process_uploads(uploads)
    except ValueError:
        return None
    if not model_files:
        return None
    multipart = [("files", (name, data, mime)) for name, data, mime in model_files]
    data: dict[str, str] = {"all_pages": "true"}
    if designation:
        data["designation"] = designation
    url = f"{settings.model_service_url.rstrip('/')}/api/v1/eskd/check"
    async with httpx.AsyncClient(timeout=settings.request_timeout_sec) as client:
        resp = await client.post(url, files=multipart, data=data)
    if resp.status_code >= 400:
        return None
    payload = resp.json()
    if extracted:
        payload["extracted_texts"] = extracted
    if warnings:
        payload["preprocess_warnings"] = warnings
    return payload


@router.post("", response_model=CheckSummaryResponse, status_code=202)
async def create_check(
    metadata: str = Form(...),
    files: list[UploadFile] = File(...),
    db: AsyncSession = Depends(get_db),
    principal: IntegrationPrincipal = Depends(require_permission("checks:write")),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    try:
        req = CheckCreateRequest.model_validate(json.loads(metadata))
    except (json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(400, f"Invalid metadata JSON: {exc}") from exc

    uploads = await _raw_uploads(files)
    limit = settings.max_upload_mb * 1024 * 1024
    if sum(len(data) for _, data in uploads) > limit:
        raise HTTPException(413, f"Upload exceeds {settings.max_upload_mb} MB")

    checksum = compute_uploads_checksum(uploads)
    card = req.document
    card.checksum = card.checksum or checksum
    request_id = _request_id(idempotency_key, req.request_id)

    jobs = IntegrationJobService(db)
    job, created = await jobs.create_or_get(
        request_id=request_id,
        card=card,
        submitted_by=principal.subject,
        ruleset_version=req.ruleset_version,
    )
    if not created:
        return await jobs.to_summary(job)

    await jobs.set_status(job, "queued")
    executor = CheckExecutor(db)
    payload: dict[str, Any] | None = None
    try:
        payload = await executor.run_cached(job_id=job.id, uploads=uploads, designation=card.designation)
    except RuntimeError:
        payload = None

    if payload is None:
        ai_payload = await _maybe_run_ai(uploads=uploads, designation=card.designation, run_ai=req.run_ai)
        if ai_payload:
            payload = await executor.persist_ai_result(
                job_id=job.id,
                payload=ai_payload,
                uploads=uploads,
            )
        else:
            await jobs.fail(job, "Нет кеша и модель ИИ недоступна")
            raise HTTPException(503, "Нет сохранённого результата и модель ИИ недоступна")

    summary = await jobs.to_summary(job)
    await WebhookService(db).enqueue_for_job(job, summary.model_dump())
    return summary


@router.post("/json", response_model=CheckSummaryResponse, status_code=202)
async def create_check_json(
    body: CheckCreateRequest,
    db: AsyncSession = Depends(get_db),
    principal: IntegrationPrincipal = Depends(require_permission("checks:write")),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    if not body.document.files:
        raise HTTPException(400, "JSON create requires pre-uploaded files in document.files metadata")
    request_id = _request_id(idempotency_key, body.request_id)
    jobs = IntegrationJobService(db)
    job, created = await jobs.create_or_get(
        request_id=request_id,
        card=body.document,
        submitted_by=principal.subject,
        ruleset_version=body.ruleset_version,
    )
    if not created:
        return await jobs.to_summary(job)
    await jobs.set_status(job, "accepted")
    return await jobs.to_summary(job)


@router.get("/{check_id}", response_model=CheckSummaryResponse)
async def get_check(
    check_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: IntegrationPrincipal = Depends(require_permission("checks:read")),
):
    job = await IntegrationJobService(db).get(check_id)
    if not job:
        raise HTTPException(404, "Check not found")
    return await IntegrationJobService(db).to_summary(job)


@router.get("/{check_id}/findings", response_model=FindingsResponse)
async def get_findings(
    check_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: IntegrationPrincipal = Depends(require_permission("checks:read")),
):
    job = await IntegrationJobService(db).get(check_id)
    if not job:
        raise HTTPException(404, "Check not found")
    return ReportService.build_findings(job)


@router.get("/{check_id}/report")
async def get_report(
    check_id: uuid.UUID,
    format: str = Query(default="pdf", alias="format"),
    db: AsyncSession = Depends(get_db),
    _: IntegrationPrincipal = Depends(require_permission("checks:read")),
):
    job = await IntegrationJobService(db).get(check_id)
    if not job:
        raise HTTPException(404, "Check not found")
    summary = (await IntegrationJobService(db).to_summary(job)).model_dump()
    if format == "json":
        return JSONResponse(ReportService.build_json_report(job, summary))
    pdf = ReportService.build_pdf_bytes(job, summary)
    return Response(content=pdf, media_type="application/pdf")


@router.post("/{check_id}/cancel", response_model=CheckSummaryResponse)
async def cancel_check(
    check_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: IntegrationPrincipal = Depends(require_permission("checks:write")),
):
    jobs = IntegrationJobService(db)
    job = await jobs.get(check_id)
    if not job:
        raise HTTPException(404, "Check not found")
    if job.status in {"completed", "completed_with_remarks", "cancelled"}:
        return await jobs.to_summary(job)
    job = await jobs.cancel(job)
    return await jobs.to_summary(job)
