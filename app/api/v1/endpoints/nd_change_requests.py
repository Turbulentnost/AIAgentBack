from __future__ import annotations

import uuid
from io import BytesIO

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession
from app.documents.storage import object_storage
from app.models.document import Document
from app.models.nd_change import NdChangeDraftFile, NdChangeRequest
from app.schemas.nd_change import (
    NdChangeApplyRequest,
    NdChangeApprovalRouteRead,
    NdChangeCandidateDocumentRead,
    NdChangeFindLocationRequest,
    NdChangePreviewRead,
    NdChangeRequestCreate,
    NdChangeRequestRead,
    NdChangeResultRead,
    NdChangeSelectDocument,
    NdChangeTargetLocationRead,
)
from app.services.nd_change_service import NdChangeService, NdChangeServiceError, document_code, generated_download_name

router = APIRouter(prefix="/nd-change-requests", tags=["nd-change-requests"])


@router.post("", response_model=NdChangeRequestRead, status_code=status.HTTP_201_CREATED)
async def create_nd_change_request(payload: NdChangeRequestCreate, db: DbSession, current_user: CurrentUser):
    try:
        item = await NdChangeService(db).create(payload, current_user=current_user)
        await db.commit()
        return item
    except NdChangeServiceError as exc:
        await db.rollback()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


@router.get("", response_model=list[NdChangeRequestRead])
async def list_nd_change_requests(db: DbSession, current_user: CurrentUser):
    return await NdChangeService(db).list(current_user=current_user)


@router.get("/{request_id}", response_model=NdChangePreviewRead)
async def get_nd_change_request(request_id: uuid.UUID, db: DbSession, current_user: CurrentUser):
    return await _preview(db, request_id)


@router.post("/{request_id}/detect-document", response_model=list[NdChangeCandidateDocumentRead])
async def detect_document(request_id: uuid.UUID, db: DbSession, current_user: CurrentUser):
    try:
        candidates = await NdChangeService(db).detect_document(request_id, current_user=current_user)
        await db.commit()
        return await _candidate_reads(db, candidates)
    except NdChangeServiceError as exc:
        await db.rollback()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


@router.post("/{request_id}/select-document", response_model=NdChangeRequestRead)
async def select_document(
    request_id: uuid.UUID,
    payload: NdChangeSelectDocument,
    db: DbSession,
    current_user: CurrentUser,
):
    try:
        item = await NdChangeService(db).select_document(
            request_id,
            document_id=payload.document_id,
            document_version_id=payload.document_version_id,
            current_user=current_user,
        )
        await db.commit()
        return item
    except NdChangeServiceError as exc:
        await db.rollback()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


@router.post("/{request_id}/find-location", response_model=list[NdChangeTargetLocationRead])
async def find_location(
    request_id: uuid.UUID,
    payload: NdChangeFindLocationRequest,
    db: DbSession,
    current_user: CurrentUser,
):
    try:
        service = NdChangeService(db)
        if payload.document_id:
            await service.select_document(
                request_id,
                document_id=payload.document_id,
                document_version_id=payload.document_version_id,
                current_user=current_user,
            )
        locations = await service.find_location(request_id, current_user=current_user)
        await db.commit()
        return locations
    except NdChangeServiceError as exc:
        await db.rollback()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


@router.post("/{request_id}/apply-changes", response_model=NdChangePreviewRead)
async def apply_changes(
    request_id: uuid.UUID,
    payload: NdChangeApplyRequest,
    db: DbSession,
    current_user: CurrentUser,
):
    try:
        await NdChangeService(db).apply_changes(
            request_id,
            current_user=current_user,
            location_id=payload.location_id,
            mark_user_reviewed=payload.mark_user_reviewed,
        )
        await db.commit()
        return await _preview(db, request_id)
    except NdChangeServiceError as exc:
        await db.rollback()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


@router.get("/{request_id}/preview", response_model=NdChangePreviewRead)
async def preview_nd_change_request(request_id: uuid.UUID, db: DbSession, current_user: CurrentUser):
    return await _preview(db, request_id)


@router.get("/{request_id}/download-draft")
async def download_draft(request_id: uuid.UUID, db: DbSession, current_user: CurrentUser):
    return await _download_file(db, request_id, "draft")


@router.get("/{request_id}/download-notice")
async def download_notice(request_id: uuid.UUID, db: DbSession, current_user: CurrentUser):
    return await _download_file(db, request_id, "notice")


@router.post("/{request_id}/send-approval", response_model=NdChangeApprovalRouteRead)
async def send_approval(
    request_id: uuid.UUID,
    payload: NdChangeApplyRequest,
    db: DbSession,
    current_user: CurrentUser,
):
    try:
        await NdChangeService(db).mark_user_reviewed(request_id, current_user=current_user)
        route = await NdChangeService(db).send_to_approval(
            request_id,
            current_user=current_user,
            approval_user_ids=payload.approval_user_ids,
        )
        await db.commit()
        return route
    except NdChangeServiceError as exc:
        await db.rollback()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


async def _preview(db: DbSession, request_id: uuid.UUID) -> NdChangePreviewRead:
    try:
        request = await NdChangeService(db).get_full(request_id)
    except NdChangeServiceError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return NdChangePreviewRead(
        request=NdChangeRequestRead.model_validate(request),
        candidates=await _candidate_reads(db, request.candidates),
        target_locations=[NdChangeTargetLocationRead.model_validate(item) for item in request.target_locations],
        operations=[item for item in request.operations],
        draft_files=[item for item in request.draft_files],
        approval_routes=[item for item in request.approval_routes],
        result=NdChangeResultRead.model_validate(request.results[-1]) if request.results else None,
    )


async def _candidate_reads(db: DbSession, candidates) -> list[NdChangeCandidateDocumentRead]:
    result = []
    for candidate in candidates:
        document = await db.get(Document, candidate.document_id)
        item = NdChangeCandidateDocumentRead.model_validate(candidate)
        item.document_title = document.title if document else None
        item.document_code = document_code(document)
        result.append(item)
    return result


async def _download_file(db: DbSession, request_id: uuid.UUID, file_type: str) -> StreamingResponse:
    request = await db.get(NdChangeRequest, request_id)
    if request is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Заявка не найдена")
    result = await db.execute(
        select(NdChangeDraftFile)
        .where(NdChangeDraftFile.change_request_id == request_id, NdChangeDraftFile.file_type == file_type)
        .order_by(NdChangeDraftFile.created_at.desc())
    )
    files = list(result.scalars().all())
    if not files:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Файл не найден")
    file = files[0]
    content = object_storage.get_object_from_bucket(file.draft_bucket, file.draft_object_name)
    filename = generated_download_name(file)
    return StreamingResponse(
        BytesIO(content),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
