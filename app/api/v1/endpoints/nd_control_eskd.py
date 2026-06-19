from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile, status

from app.api.deps import CurrentUser, DbSession
from app.documents.storage import ObjectStorageError
from app.eskd.constants import ESKD_MODULE_VERSION, ND_CONTROL_AGENT_SLUG
from app.models.enums import EskdDocumentKind, EskdRegistrationStatus
from app.schemas.eskd import (
    EskdDocumentRegistrationRead,
    EskdDocumentUploadRegisterRequest,
    EskdModuleInfoRead,
    EskdRegisterExistingRequest,
    EskdRegistrationListResponse,
    EskdUploadRegisterResponse,
    EskdValidationReportRead,
    EskdCheckResultRead,
)
from app.services.eskd_registration_service import EskdRegistrationService, EskdRegistrationServiceError
from app.services.eskd_validation_service import EskdValidationService, EskdValidationServiceError
from app.services.nd_control_permission import can_access_nd_control_agent

router = APIRouter(prefix="/eskd", tags=["nd-control-eskd"])


async def _require_nd_agent_access(db: DbSession, user: CurrentUser) -> None:
    if not await can_access_nd_control_agent(db, user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Нет доступа к надстройке ЕСКД (агент контроля НД)")


@router.get("/info", response_model=EskdModuleInfoRead)
async def eskd_module_info(db: DbSession, current_user: CurrentUser):
    await _require_nd_agent_access(db, current_user)
    return EskdModuleInfoRead(
        version=ESKD_MODULE_VERSION,
        agent_slug=ND_CONTROL_AGENT_SLUG,
        capabilities=[
            "upload_and_register",
            "register_existing_document",
            "list_registrations",
            "validate_eskd_compliance",
            "get_validation_report",
        ],
        supported_document_kinds=[item.value for item in EskdDocumentKind],
    )


@router.get("/registrations", response_model=EskdRegistrationListResponse)
async def list_eskd_registrations(
    db: DbSession,
    current_user: CurrentUser,
    page: int = Query(default=1, ge=1),
    size: int = Query(default=50, ge=1, le=200),
    status: EskdRegistrationStatus | None = None,
    designation: str | None = None,
):
    await _require_nd_agent_access(db, current_user)
    items, total = await EskdRegistrationService(db).list_registrations(
        page=page,
        size=size,
        status=status,
        designation_query=designation,
    )
    return EskdRegistrationListResponse(
        items=[EskdDocumentRegistrationRead.model_validate(item) for item in items],
        total=total,
        page=page,
        size=size,
    )


@router.get("/registrations/{registration_id}", response_model=EskdDocumentRegistrationRead)
async def get_eskd_registration(registration_id: uuid.UUID, db: DbSession, current_user: CurrentUser):
    await _require_nd_agent_access(db, current_user)
    try:
        item = await EskdRegistrationService(db).get_registration(registration_id)
        return EskdDocumentRegistrationRead.model_validate(item)
    except EskdRegistrationServiceError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post(
    "/documents/upload-register",
    response_model=EskdUploadRegisterResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_and_register_eskd_document(
    db: DbSession,
    current_user: CurrentUser,
    file: Annotated[UploadFile, File(...)],
    title: Annotated[str | None, Form()] = None,
    designation: Annotated[str | None, Form()] = None,
    document_kind: Annotated[EskdDocumentKind, Form()] = EskdDocumentKind.OTHER,
    owner_department: Annotated[str | None, Form()] = None,
    nd_control_department_id: Annotated[uuid.UUID | None, Form()] = None,
    department_id: Annotated[uuid.UUID | None, Form()] = None,
    notes: Annotated[str | None, Form()] = None,
    relative_path: Annotated[str | None, Form()] = None,
    start_processing: Annotated[bool, Form()] = True,
    is_knowledge_base: Annotated[bool, Form()] = True,
):
    await _require_nd_agent_access(db, current_user)
    content = await file.read()
    payload = EskdDocumentUploadRegisterRequest(
        title=title,
        designation=designation,
        document_kind=document_kind,
        owner_department=owner_department,
        nd_control_department_id=nd_control_department_id,
        department_id=department_id,
        notes=notes,
        relative_path=relative_path,
        start_processing=start_processing,
        is_knowledge_base=is_knowledge_base,
    )
    try:
        result = await EskdRegistrationService(db).upload_and_register(
            content=content,
            mime_type=file.content_type or "application/octet-stream",
            original_filename=file.filename,
            payload=payload,
            current_user=current_user,
        )
        await db.commit()
        return EskdUploadRegisterResponse(
            registration=EskdDocumentRegistrationRead.model_validate(result.registration),
            document=result.document,
            document_card=result.document_card,
            processing_queued=result.processing_queued,
            celery_task_id=result.celery_task_id,
        )
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except ObjectStorageError as exc:
        await db.rollback()
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except EskdRegistrationServiceError as exc:
        await db.rollback()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post(
    "/documents/{document_id}/register",
    response_model=EskdUploadRegisterResponse,
    status_code=status.HTTP_201_CREATED,
)
async def register_existing_document_for_eskd(
    document_id: uuid.UUID,
    payload: EskdRegisterExistingRequest,
    db: DbSession,
    current_user: CurrentUser,
):
    await _require_nd_agent_access(db, current_user)
    try:
        result = await EskdRegistrationService(db).register_existing_document(
            document_id,
            payload=payload,
            current_user=current_user,
        )
        await db.commit()
        return EskdUploadRegisterResponse(
            registration=EskdDocumentRegistrationRead.model_validate(result.registration),
            document=result.document,
            document_card=result.document_card,
            processing_queued=result.processing_queued,
            celery_task_id=result.celery_task_id,
        )
    except EskdRegistrationServiceError as exc:
        await db.rollback()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


def _validation_report_read(
    report,
    *,
    registration_status: EskdRegistrationStatus | None = None,
) -> EskdValidationReportRead:
    payload = report.to_dict()
    return EskdValidationReportRead(
        passed=payload["passed"],
        score=payload["score"],
        summary=payload["summary"],
        errors_count=payload["errors_count"],
        warnings_count=payload["warnings_count"],
        checks=[EskdCheckResultRead.model_validate(item) for item in payload["checks"]],
        document_id=payload.get("document_id"),
        registration_id=payload.get("registration_id"),
        designation=payload.get("designation"),
        document_kind=payload.get("document_kind"),
        text_available=bool(payload.get("text_available")),
        validated_at=payload["validated_at"],
        registration_status=registration_status,
    )


@router.post("/registrations/{registration_id}/validate", response_model=EskdValidationReportRead)
async def validate_eskd_registration(registration_id: uuid.UUID, db: DbSession, current_user: CurrentUser):
    await _require_nd_agent_access(db, current_user)
    try:
        report = await EskdValidationService(db).validate_registration(registration_id)
        registration = await EskdRegistrationService(db).get_registration(registration_id)
        await db.commit()
        return _validation_report_read(report, registration_status=registration.status)
    except EskdValidationServiceError as exc:
        await db.rollback()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/registrations/{registration_id}/validation", response_model=EskdValidationReportRead)
async def get_eskd_validation_report(registration_id: uuid.UUID, db: DbSession, current_user: CurrentUser):
    await _require_nd_agent_access(db, current_user)
    try:
        registration = await EskdRegistrationService(db).get_registration(registration_id)
        report = await EskdValidationService(db).get_validation_report(registration_id)
        return _validation_report_read(report, registration_status=registration.status)
    except (EskdRegistrationServiceError, EskdValidationServiceError) as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post("/documents/{document_id}/validate", response_model=EskdValidationReportRead)
async def validate_eskd_document(document_id: uuid.UUID, db: DbSession, current_user: CurrentUser):
    await _require_nd_agent_access(db, current_user)
    try:
        report = await EskdValidationService(db).validate_document(document_id)
        registration = await EskdRegistrationService(db).get_registration_by_document(document_id)
        status_value = registration.status if registration else None
        await db.commit()
        return _validation_report_read(report, registration_status=status_value)
    except EskdValidationServiceError as exc:
        await db.rollback()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
