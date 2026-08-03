from __future__ import annotations

import html
import uuid

from fastapi import APIRouter, File, HTTPException, Query, UploadFile, status
from fastapi.responses import HTMLResponse

from app.api.deps import CurrentUser, DbSession
from app.models.enums import NdDevelopmentRequestKind, NdDevelopmentRequestStatus, NdReportKind
from app.schemas.nd_development_request import (
    NdDevelopmentDuplicateCheckRead,
    NdDevelopmentPackageCheckRead,
    NdDevelopmentRequestCreate,
    NdDevelopmentRequestPage,
    NdDevelopmentRequestRead,
)
from app.schemas.turbo_smk import (
    NdAcknowledgementConfirm,
    NdAcknowledgementCreate,
    NdAcknowledgementPage,
    NdAcknowledgementRead,
    NdBulkImportRequest,
    NdBulkImportResult,
    NdDocumentValidationReport,
    NdImpactAnalysisRequest,
    NdImpactAnalysisReport,
    NdReportResult,
    NdVisioImportResult,
)
from app.services.nd_acknowledgement_service import NdAcknowledgementService, NdAcknowledgementServiceError
from app.services.nd_bulk_import_service import NdBulkImportService
from app.services.nd_development_request_service import NdDevelopmentRequestService, NdDevelopmentRequestServiceError
from app.services.nd_document_validation_service import NdDocumentValidationService
from app.services.nd_erp_integration_service import NdErpIntegrationService
from app.services.nd_impact_analysis_service import NdImpactAnalysisService
from app.services.nd_reports_service import NdReportsService
from app.services.nd_visio_service import NdVisioService
from app.services.nd_control_permission import can_access_nd_control_agent, can_manage_nd_control_templates

router = APIRouter(tags=["nd-control-turbo-smk"])


async def _require_access(db: DbSession, user: CurrentUser) -> None:
    if not await can_access_nd_control_agent(db, user) and not await can_manage_nd_control_templates(db, user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Нет доступа к модулю Турбо СМК")


@router.get("/dashboard", response_class=HTMLResponse)
async def turbo_smk_dashboard(db: DbSession, current_user: CurrentUser):
    await _require_access(db, current_user)
    sections = [
        ("База НД", "/api/v1/document-cards/viewer"),
        ("Шаблоны", "/api/v1/nd-control/templates"),
        ("Заявки на НД", "/api/v1/nd-control/development-requests"),
        ("Извещения", "/api/v1/nd-change-requests"),
        ("Журнал изменений", "/api/v1/nd-control/change-journal"),
        ("Проверка документов", "/api/v1/nd-control/documents"),
        ("Анализ влияния", "/api/v1/nd-control/impact-analysis"),
        ("Процессы / UML", "/api/v1/nd-control/departments"),
        ("Ознакомление", "/api/v1/nd-control/acknowledgements"),
        ("Отчёты", "/api/v1/nd-control/reports"),
        ("Импорт базы", "/api/v1/nd-control/import"),
        ("1C ERP", "/api/v1/nd-control/erp/status"),
    ]
    rows = "".join(
        f"<li><a href=\"{html.escape(url)}\">{html.escape(title)}</a></li>" for title, url in sections
    )
    return f"""<!DOCTYPE html>
<html lang="ru"><head><meta charset="utf-8"><title>Турбо СМК</title></head>
<body><h1>Турбо СМК — панель разделов</h1><ul>{rows}</ul></body></html>"""


@router.post("/development-requests", response_model=NdDevelopmentRequestRead, status_code=status.HTTP_201_CREATED)
async def create_development_request(payload: NdDevelopmentRequestCreate, db: DbSession, current_user: CurrentUser):
    await _require_access(db, current_user)
    try:
        item = await NdDevelopmentRequestService(db).create(payload, current_user=current_user)
        await db.commit()
        return item
    except NdDevelopmentRequestServiceError as exc:
        await db.rollback()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/development-requests", response_model=NdDevelopmentRequestPage)
async def list_development_requests(
    db: DbSession,
    current_user: CurrentUser,
    kind: NdDevelopmentRequestKind | None = None,
    status_filter: NdDevelopmentRequestStatus | None = Query(default=None, alias="status"),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=50, ge=1, le=200),
):
    await _require_access(db, current_user)
    items, total = await NdDevelopmentRequestService(db).list(kind=kind, status=status_filter, page=page, size=size)
    return NdDevelopmentRequestPage(
        items=[NdDevelopmentRequestRead.model_validate(item) for item in items],
        total=total,
        page=page,
        size=size,
    )


@router.post("/development-requests/{request_id}/submit", response_model=NdDevelopmentRequestRead)
async def submit_development_request(request_id: uuid.UUID, db: DbSession, current_user: CurrentUser):
    await _require_access(db, current_user)
    try:
        item = await NdDevelopmentRequestService(db).submit(request_id)
        await db.commit()
        return item
    except NdDevelopmentRequestServiceError as exc:
        await db.rollback()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/development-requests/{request_id}/duplicate-check", response_model=NdDevelopmentDuplicateCheckRead)
async def duplicate_check_development_request(request_id: uuid.UUID, db: DbSession, current_user: CurrentUser):
    await _require_access(db, current_user)
    try:
        payload = await NdDevelopmentRequestService(db).run_duplicate_check(request_id)
        await db.commit()
        return NdDevelopmentDuplicateCheckRead(
            request_id=request_id,
            matches=payload["matches"],
            recommendation=payload["recommendation"],
        )
    except NdDevelopmentRequestServiceError as exc:
        await db.rollback()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/development-requests/{request_id}/package-check", response_model=NdDevelopmentPackageCheckRead)
async def package_check_development_request(request_id: uuid.UUID, db: DbSession, current_user: CurrentUser):
    await _require_access(db, current_user)
    try:
        payload = await NdDevelopmentRequestService(db).check_package(request_id)
        await db.commit()
        return NdDevelopmentPackageCheckRead(
            request_id=request_id,
            is_complete=payload["is_complete"],
            missing_items=payload["missing_items"],
            warnings=payload["warnings"],
        )
    except NdDevelopmentRequestServiceError as exc:
        await db.rollback()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/documents/{document_id}/validate", response_model=NdDocumentValidationReport)
async def validate_document(document_id: uuid.UUID, db: DbSession, current_user: CurrentUser):
    await _require_access(db, current_user)
    try:
        return await NdDocumentValidationService(db).validate_document(document_id)
    except ValueError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post("/impact-analysis", response_model=NdImpactAnalysisReport)
async def analyze_impact(payload: NdImpactAnalysisRequest, db: DbSession, current_user: CurrentUser):
    await _require_access(db, current_user)
    return await NdImpactAnalysisService(db).analyze(
        document_id=payload.document_id,
        change_text=payload.change_text,
    )


@router.post("/acknowledgements", response_model=list[NdAcknowledgementRead], status_code=status.HTTP_201_CREATED)
async def create_acknowledgements(payload: NdAcknowledgementCreate, db: DbSession, current_user: CurrentUser):
    await _require_access(db, current_user)
    try:
        items = await NdAcknowledgementService(db).assign(payload)
        await db.commit()
        return [NdAcknowledgementRead.model_validate(item) for item in items]
    except NdAcknowledgementServiceError as exc:
        await db.rollback()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/acknowledgements/me", response_model=NdAcknowledgementPage)
async def list_my_acknowledgements(
    db: DbSession,
    current_user: CurrentUser,
    page: int = Query(default=1, ge=1),
    size: int = Query(default=50, ge=1, le=200),
):
    items, total = await NdAcknowledgementService(db).list_for_user(current_user, page=page, size=size)
    return NdAcknowledgementPage(
        items=[NdAcknowledgementRead.model_validate(item) for item in items],
        total=total,
        page=page,
        size=size,
    )


@router.post("/acknowledgements/{assignment_id}/confirm", response_model=NdAcknowledgementRead)
async def confirm_acknowledgement(
    assignment_id: uuid.UUID,
    payload: NdAcknowledgementConfirm,
    db: DbSession,
    current_user: CurrentUser,
):
    try:
        item = await NdAcknowledgementService(db).confirm(assignment_id, user=current_user, note=payload.note)
        await db.commit()
        return NdAcknowledgementRead.model_validate(item)
    except NdAcknowledgementServiceError as exc:
        await db.rollback()
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.get("/reports", response_model=list[dict])
async def list_reports(db: DbSession, current_user: CurrentUser):
    await _require_access(db, current_user)
    return await NdReportsService(db).list_available()


@router.post("/reports/{report_kind}", response_model=NdReportResult)
async def generate_report(report_kind: NdReportKind, db: DbSession, current_user: CurrentUser):
    await _require_access(db, current_user)
    return await NdReportsService(db).generate(report_kind)


@router.post("/visio/import", response_model=NdVisioImportResult)
async def import_visio(
    db: DbSession,
    current_user: CurrentUser,
    file: UploadFile = File(...),
):
    await _require_access(db, current_user)
    content = await file.read()
    return NdVisioService().import_vsdx(filename=file.filename or "diagram.vsdx", content=content)


@router.post("/import/folder", response_model=NdBulkImportResult)
async def import_nd_folder(payload: NdBulkImportRequest, db: DbSession, current_user: CurrentUser):
    await _require_access(db, current_user)
    result = await NdBulkImportService().import_folder(db, payload)
    if not payload.dry_run:
        await db.commit()
    return result


@router.get("/erp/status")
async def erp_integration_status(db: DbSession, current_user: CurrentUser):
    await _require_access(db, current_user)
    return await NdErpIntegrationService().pull_org_structure()
