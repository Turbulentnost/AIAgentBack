from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, status

from app.api.deps import CurrentUser, DbSession
from app.schemas.nd_control_analysis import (
    ConfirmProcessOwnerRequest,
    DepartmentAnalysisRunPage,
    DepartmentAnalysisRunRead,
    DepartmentAnalysisStartRequest,
    DepartmentAnalysisStatusRead,
    DepartmentDocumentCardPage,
    DepartmentProcessPage,
    DepartmentRelationPage,
    DepartmentReviewPendingRead,
    DepartmentSummaryRead,
    BulkApproveRelationsRequest,
    BulkApproveRelationsResponse,
    NdControlDepartmentCreateResponse,
    ProcessUmlResponse,
)
from app.schemas.nd_change_journal import NdChangeJournalEntryPage, NdChangeJournalEntryRead
from app.schemas.nd_control_registry import (
    NdControlDepartmentCreate,
    NdControlDepartmentKnowledgeBasesUpdate,
    NdControlDepartmentRead,
    NdControlDepartmentUpdate,
    NdControlPermissionsRead,
    NdControlTemplateSourceRead,
    NdTemplateTypeOption,
    NdDocumentCardPage,
    NdDocumentCardRead,
    NdDocumentCardUpdate,
)
from app.schemas.nd_control_templates import (
    NdControlTemplateDetail,
    NdControlTemplateDocumentCreate,
    NdControlTemplateDocumentPage,
    NdControlTemplateDocumentRead,
    NdControlTemplateDocumentUpdate,
    NdControlTemplateKnowledgeBasesUpdate,
    NdControlTemplatePage,
    NdControlTemplateRead,
    NdControlTemplateUpdate,
)
from app.services.department_analysis_dispatch import (
    enqueue_department_analysis_run,
    maybe_recover_stale_pending_run,
)
from app.services.department_analysis_service import DepartmentAnalysisService
from app.services.nd_control_department_service import (
    NdControlDepartmentService,
    NdControlDepartmentServiceError,
)
from app.services.nd_control_permission import (
    can_access_nd_control_agent,
    can_manage_nd_control_departments,
    can_manage_nd_control_templates,
    can_reanalyze_nd_control_departments,
    can_upload_template_documents,
    can_view_nd_change_journal,
    is_process_management_specialist_position,
)
from app.services.nd_change_journal_service import NdChangeJournalService, NdChangeJournalServiceError
from app.services.nd_control_department_detail_service import (
    NdControlDepartmentDetailService,
    NdControlDepartmentDetailServiceError,
)
from app.services.nd_document_card_service import NdDocumentCardService, NdDocumentCardServiceError
from app.services.nd_control_template_service import (
    NdControlTemplateService,
    NdControlTemplateServiceError,
)
from app.services.nd_process_uml_service import NdProcessUmlService, NdProcessUmlServiceError
from app.api.v1.endpoints.nd_control_eskd import router as eskd_router
from app.api.v1.endpoints.nd_control_turbo_smk import router as turbo_smk_router
from app.models.enums import (
    NdChangeJournalEventType,
    NdChangeJournalSource,
    NdTemplateClassificationStatus,
    NdTemplateType,
)
from app.utils.nd_template_classification import ND_TEMPLATE_TYPE_LABELS
from app.workers.tasks import classify_template_document

router = APIRouter(prefix="/nd-control", tags=["nd-control"])
router.include_router(eskd_router)
router.include_router(turbo_smk_router)


async def _require_agent_access(db: DbSession, user: CurrentUser) -> None:
    if not await can_access_nd_control_agent(db, user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Нет доступа к агенту контроля НД")


async def _require_manage_access(db: DbSession, user: CurrentUser) -> None:
    if not await can_manage_nd_control_departments(db, user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Недостаточно прав для управления отделами")


async def _require_template_manage_access(db: DbSession, user: CurrentUser) -> None:
    if not await can_manage_nd_control_templates(db, user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Недостаточно прав для управления шаблонами")


async def _require_template_upload_access(db: DbSession, user: CurrentUser) -> None:
    if not await can_upload_template_documents(db, user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Недостаточно прав для загрузки документов шаблонов")


async def _require_template_read_access(db: DbSession, user: CurrentUser) -> None:
    if (
        not await can_access_nd_control_agent(db, user)
        and not await can_manage_nd_control_templates(db, user)
        and not await can_upload_template_documents(db, user)
    ):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Нет доступа к шаблонам НД")


async def _require_reanalyze_access(db: DbSession, user: CurrentUser) -> None:
    if not await can_reanalyze_nd_control_departments(db, user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Недостаточно прав для запуска анализа отделов")


async def _require_change_journal_access(db: DbSession, user: CurrentUser) -> None:
    if not await can_view_nd_change_journal(db, user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Недостаточно прав для просмотра журнала изменений НД")


def _department_read(item: dict) -> NdControlDepartmentRead:
    dept = item["department"]
    base = NdControlDepartmentRead.model_validate(dept)
    return base.model_copy(
        update={
            "knowledge_bases_count": item["knowledge_bases_count"],
            "cards_count": item["cards_count"],
            "documents_count": item.get("documents_count", 0),
            "processes_count": item.get("processes_count", 0),
            "pending_review_count": item.get("pending_review_count", 0),
            "knowledge_base_ids": item["knowledge_base_ids"],
            "analysis_status": item.get("analysis_status"),
            "analysis_progress_percent": item.get("analysis_progress_percent"),
        }
    )


@router.get("/me/permissions", response_model=NdControlPermissionsRead)
async def nd_control_permissions(db: DbSession, current_user: CurrentUser):
    return NdControlPermissionsRead(
        can_manage_departments=await can_manage_nd_control_departments(db, current_user),
        can_access_agent=await can_access_nd_control_agent(db, current_user),
        can_manage_templates=await can_manage_nd_control_templates(db, current_user),
        can_upload_template_documents=await can_upload_template_documents(db, current_user),
        can_reanalyze_departments=await can_reanalyze_nd_control_departments(db, current_user),
        can_view_change_journal=await can_view_nd_change_journal(db, current_user),
        is_process_management_specialist=is_process_management_specialist_position(current_user.position),
    )


@router.get("/change-journal", response_model=NdChangeJournalEntryPage)
async def list_nd_change_journal(
    db: DbSession,
    current_user: CurrentUser,
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
    event_type: NdChangeJournalEventType | None = None,
    department_id: uuid.UUID | None = None,
    template_id: uuid.UUID | None = None,
    actor_id: uuid.UUID | None = None,
    search: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=50, ge=1, le=200),
):
    await _require_change_journal_access(db, current_user)
    items, total = await NdChangeJournalService(db).list_entries(
        date_from=date_from,
        date_to=date_to,
        event_type=event_type,
        department_id=department_id,
        template_id=template_id,
        actor_id=actor_id,
        search=search,
        page=page,
        size=size,
    )
    return NdChangeJournalEntryPage(
        items=[NdChangeJournalEntryRead.model_validate(item) for item in items],
        total=total,
        page=page,
        size=size,
    )


@router.get("/change-journal/{entry_id}", response_model=NdChangeJournalEntryRead)
async def get_nd_change_journal_entry(
    entry_id: uuid.UUID,
    db: DbSession,
    current_user: CurrentUser,
):
    await _require_change_journal_access(db, current_user)
    try:
        return NdChangeJournalEntryRead.model_validate(
            await NdChangeJournalService(db).get_entry_or_raise(entry_id)
        )
    except NdChangeJournalServiceError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.get("/templates/types", response_model=list[NdTemplateTypeOption])
async def list_nd_template_types(db: DbSession, current_user: CurrentUser):
    await _require_template_read_access(db, current_user)
    return [
        NdTemplateTypeOption(value=value, label=label)
        for value, label in ND_TEMPLATE_TYPE_LABELS.items()
    ]


@router.get("/templates/sources", response_model=list[NdControlTemplateSourceRead])
async def list_nd_template_sources(
    db: DbSession,
    current_user: CurrentUser,
    knowledge_base_id: uuid.UUID | None = None,
    query: str | None = Query(default=None),
    include_registered: bool = False,
):
    await _require_template_read_access(db, current_user)
    items = await NdControlTemplateService(db).list_sources(
        user=current_user,
        knowledge_base_id=knowledge_base_id,
        query=query,
        include_registered=include_registered,
    )
    return [NdControlTemplateSourceRead(**item) for item in items]


@router.get("/templates", response_model=NdControlTemplatePage)
async def list_nd_templates(
    db: DbSession,
    current_user: CurrentUser,
    template_type: NdTemplateType | None = None,
    query: str | None = Query(default=None),
    active_only: bool = True,
    page: int = Query(default=1, ge=1),
    size: int = Query(default=50, ge=1, le=200),
):
    await _require_template_read_access(db, current_user)
    items, total = await NdControlTemplateService(db).list_templates(
        template_type=template_type,
        query=query,
        active_only=active_only,
        page=page,
        size=size,
    )
    return NdControlTemplatePage(
        items=[NdControlTemplateRead(**item) for item in items],
        total=total,
        page=page,
        size=size,
    )


@router.get("/templates/{template_id}", response_model=NdControlTemplateDetail)
async def get_nd_template(
    template_id: uuid.UUID,
    db: DbSession,
    current_user: CurrentUser,
):
    await _require_template_read_access(db, current_user)
    service = NdControlTemplateService(db)
    try:
        return NdControlTemplateDetail(**await service.get_template_detail_or_raise(template_id))
    except NdControlTemplateServiceError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.patch("/templates/{template_id}", response_model=NdControlTemplateRead)
async def update_nd_template(
    template_id: uuid.UUID,
    payload: NdControlTemplateUpdate,
    db: DbSession,
    current_user: CurrentUser,
):
    await _require_template_manage_access(db, current_user)
    service = NdControlTemplateService(db)
    try:
        template = await service.update_template(
            template_id,
            values=payload.model_dump(exclude_unset=True),
            current_user=current_user,
        )
        template_id = template.id
        await db.commit()
        return NdControlTemplateRead(**await service.get_template_detail_or_raise(template_id))
    except NdControlTemplateServiceError as exc:
        await db.rollback()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.put("/templates/{template_id}/knowledge-bases", response_model=NdControlTemplateDetail)
async def set_nd_template_knowledge_bases(
    template_id: uuid.UUID,
    payload: NdControlTemplateKnowledgeBasesUpdate,
    db: DbSession,
    current_user: CurrentUser,
):
    await _require_template_manage_access(db, current_user)
    service = NdControlTemplateService(db)
    try:
        await service.set_template_knowledge_bases(
            template_id,
            payload.knowledge_base_ids,
            current_user=current_user,
        )
        await db.commit()
        return NdControlTemplateDetail(**await service.get_template_detail_or_raise(template_id))
    except NdControlTemplateServiceError as exc:
        await db.rollback()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/templates/{template_id}/documents", response_model=NdControlTemplateDocumentPage)
async def list_nd_template_documents(
    template_id: uuid.UUID,
    db: DbSession,
    current_user: CurrentUser,
    classification_status: NdTemplateClassificationStatus | None = None,
    page: int = Query(default=1, ge=1),
    size: int = Query(default=50, ge=1, le=200),
):
    await _require_template_read_access(db, current_user)
    try:
        items, total = await NdControlTemplateService(db).list_template_documents(
            template_id,
            classification_status=classification_status,
            page=page,
            size=size,
        )
        return NdControlTemplateDocumentPage(
            items=[NdControlTemplateDocumentRead(**item) for item in items],
            total=total,
            page=page,
            size=size,
        )
    except NdControlTemplateServiceError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post(
    "/templates/{template_id}/documents",
    response_model=NdControlTemplateDocumentRead,
    status_code=status.HTTP_201_CREATED,
)
async def add_nd_template_document(
    template_id: uuid.UUID,
    payload: NdControlTemplateDocumentCreate,
    db: DbSession,
    current_user: CurrentUser,
):
    await _require_template_upload_access(db, current_user)
    service = NdControlTemplateService(db)
    try:
        link = await service.add_template_document(
            template_id,
            current_user=current_user,
            knowledge_base_source_id=payload.knowledge_base_source_id,
            document_id=payload.document_id,
        )
        link_id = link.id
        await db.commit()
        classify_template_document.delay(str(link_id))
        item = await service.get_template_document_detail_or_raise(template_id, link_id)
        return NdControlTemplateDocumentRead(**item)
    except NdControlTemplateServiceError as exc:
        await db.rollback()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.patch("/templates/{template_id}/documents/{document_link_id}", response_model=NdControlTemplateDocumentRead)
async def update_nd_template_document(
    template_id: uuid.UUID,
    document_link_id: uuid.UUID,
    payload: NdControlTemplateDocumentUpdate,
    db: DbSession,
    current_user: CurrentUser,
):
    await _require_template_upload_access(db, current_user)
    if not payload.confirm_detected_type:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Нет изменений для документа шаблона")
    try:
        item = await NdControlTemplateService(db).confirm_template_document_type(
            template_id,
            document_link_id,
            current_user=current_user,
        )
        await db.commit()
        return NdControlTemplateDocumentRead(**item)
    except NdControlTemplateServiceError as exc:
        await db.rollback()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.delete("/templates/{template_id}/documents/{document_link_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_nd_template_document(
    template_id: uuid.UUID,
    document_link_id: uuid.UUID,
    db: DbSession,
    current_user: CurrentUser,
):
    await _require_template_manage_access(db, current_user)
    try:
        await NdControlTemplateService(db).delete_template_document(
            template_id,
            document_link_id,
            current_user=current_user,
        )
        await db.commit()
    except NdControlTemplateServiceError as exc:
        await db.rollback()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/departments", response_model=list[NdControlDepartmentRead])
async def list_nd_control_departments(db: DbSession, current_user: CurrentUser):
    await _require_agent_access(db, current_user)
    items = await NdControlDepartmentService(db).list_departments()
    return [_department_read(item) for item in items]


@router.post("/departments", response_model=NdControlDepartmentCreateResponse, status_code=status.HTTP_201_CREATED)
async def create_nd_control_department(
    payload: NdControlDepartmentCreate,
    db: DbSession,
    current_user: CurrentUser,
    background_tasks: BackgroundTasks,
):
    await _require_manage_access(db, current_user)
    service = NdControlDepartmentService(db)
    analysis_service = DepartmentAnalysisService(db)
    try:
        dept = await service.create_department(
            name=payload.name,
            description=payload.description,
            knowledge_base_ids=payload.knowledge_base_ids,
            current_user=current_user,
        )
        analysis_run = None
        if payload.auto_start_analysis:
            analysis_run = await analysis_service.start_department_analysis(dept.id)
            await enqueue_department_analysis_run(db, analysis_run, False, background_tasks)
            await NdChangeJournalService(db).log_event(
                event_type=NdChangeJournalEventType.DEPARTMENT_ANALYSIS_STARTED,
                actor_user_id=current_user.id,
                resource_type="department_analysis_run",
                resource_id=analysis_run.id,
                department_id=dept.id,
                summary=f"Запущен анализ отдела «{dept.name}»",
                source=NdChangeJournalSource.MANUAL,
                payload={"force_reextract": False},
            )
        await db.commit()
        items = await service.list_departments(active_only=True)
        match = next((item for item in items if item["department"].id == dept.id), None)
        if match is None:
            raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Отдел создан, но не найден")
        return NdControlDepartmentCreateResponse(
            department=_department_read(match),
            analysis_run=DepartmentAnalysisRunRead.model_validate(analysis_run) if analysis_run else None,
        )
    except NdControlDepartmentServiceError as exc:
        await db.rollback()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.patch("/departments/{department_id}", response_model=NdControlDepartmentRead)
async def update_nd_control_department(
    department_id: uuid.UUID,
    payload: NdControlDepartmentUpdate,
    db: DbSession,
    current_user: CurrentUser,
):
    await _require_manage_access(db, current_user)
    service = NdControlDepartmentService(db)
    try:
        await service.update_department(
            department_id,
            current_user=current_user,
            name=payload.name,
            description=payload.description,
            sort_order=payload.sort_order,
        )
        await db.commit()
        items = await service.list_departments(active_only=False)
        match = next((item for item in items if item["department"].id == department_id), None)
        if match is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Отдел не найден")
        return _department_read(match)
    except NdControlDepartmentServiceError as exc:
        await db.rollback()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.delete("/departments/{department_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_nd_control_department(
    department_id: uuid.UUID,
    db: DbSession,
    current_user: CurrentUser,
):
    await _require_manage_access(db, current_user)
    try:
        await NdControlDepartmentService(db).delete_department(department_id, current_user=current_user)
        await db.commit()
    except NdControlDepartmentServiceError as exc:
        await db.rollback()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.put("/departments/{department_id}/knowledge-bases", response_model=NdControlDepartmentRead)
async def set_nd_control_department_knowledge_bases(
    department_id: uuid.UUID,
    payload: NdControlDepartmentKnowledgeBasesUpdate,
    db: DbSession,
    current_user: CurrentUser,
    background_tasks: BackgroundTasks,
):
    await _require_manage_access(db, current_user)
    service = NdControlDepartmentService(db)
    analysis_service = DepartmentAnalysisService(db)
    try:
        await service.set_department_knowledge_bases(
            department_id,
            payload.knowledge_base_ids,
            current_user=current_user,
        )
        analysis_run = await analysis_service.start_department_analysis(department_id)
        await enqueue_department_analysis_run(db, analysis_run, False, background_tasks)
        await NdChangeJournalService(db).log_event(
            event_type=NdChangeJournalEventType.DEPARTMENT_ANALYSIS_STARTED,
            actor_user_id=current_user.id,
            resource_type="department_analysis_run",
            resource_id=analysis_run.id,
            department_id=department_id,
            summary="Запущен анализ отдела после изменения баз знаний",
            source=NdChangeJournalSource.MANUAL,
            payload={"force_reextract": False, "knowledge_base_ids": [str(item) for item in payload.knowledge_base_ids]},
        )
        await db.commit()
        items = await service.list_departments(active_only=False)
        match = next((item for item in items if item["department"].id == department_id), None)
        if match is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Отдел не найден")
        return _department_read(match)
    except NdControlDepartmentServiceError as exc:
        await db.rollback()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/departments/{department_id}/analyze", response_model=DepartmentAnalysisRunRead)
async def analyze_nd_control_department(
    department_id: uuid.UUID,
    payload: DepartmentAnalysisStartRequest,
    db: DbSession,
    current_user: CurrentUser,
    background_tasks: BackgroundTasks,
):
    await _require_reanalyze_access(db, current_user)
    try:
        run = await DepartmentAnalysisService(db).start_department_analysis(
            department_id,
            force_reextract=payload.force_reextract,
        )
        await enqueue_department_analysis_run(db, run, payload.force_reextract, background_tasks)
        await NdChangeJournalService(db).log_event(
            event_type=NdChangeJournalEventType.DEPARTMENT_ANALYSIS_STARTED,
            actor_user_id=current_user.id,
            resource_type="department_analysis_run",
            resource_id=run.id,
            department_id=department_id,
            summary="Запущен анализ отдела",
            source=NdChangeJournalSource.MANUAL,
            payload={"force_reextract": payload.force_reextract},
        )
        await db.commit()
        return DepartmentAnalysisRunRead.model_validate(run)
    except NdControlDepartmentServiceError as exc:
        await db.rollback()
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post("/departments/{department_id}/analyze/cancel", response_model=DepartmentAnalysisRunRead)
async def cancel_nd_control_department_analysis(
    department_id: uuid.UUID,
    db: DbSession,
    current_user: CurrentUser,
):
    await _require_reanalyze_access(db, current_user)
    try:
        run = await DepartmentAnalysisService(db).cancel_department_analysis(department_id)
        await db.commit()
        if run is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Нет активного анализа для остановки")
        return DepartmentAnalysisRunRead.model_validate(run)
    except NdControlDepartmentServiceError as exc:
        await db.rollback()
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.get("/departments/{department_id}/analysis-status", response_model=DepartmentAnalysisStatusRead)
async def get_nd_control_department_analysis_status(
    department_id: uuid.UUID,
    db: DbSession,
    current_user: CurrentUser,
    background_tasks: BackgroundTasks,
):
    await _require_agent_access(db, current_user)
    try:
        service = DepartmentAnalysisService(db)
        latest_run = await service.get_latest_run(department_id)
        if latest_run is not None:
            await maybe_recover_stale_pending_run(db, latest_run, background_tasks)
        status_payload = await service.get_analysis_status(department_id)
        return DepartmentAnalysisStatusRead.model_validate(status_payload)
    except NdControlDepartmentServiceError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.get("/departments/{department_id}/summary", response_model=DepartmentSummaryRead)
async def get_nd_control_department_summary(
    department_id: uuid.UUID,
    db: DbSession,
    current_user: CurrentUser,
):
    await _require_agent_access(db, current_user)
    try:
        summary = await DepartmentAnalysisService(db).get_department_summary(department_id)
        last_run = summary.pop("last_analysis_run", None)
        return DepartmentSummaryRead.model_validate(
            {
                **summary,
                "last_analysis_run": DepartmentAnalysisRunRead.model_validate(last_run) if last_run else None,
            }
        )
    except NdControlDepartmentServiceError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.get("/departments/{department_id}/document-cards", response_model=DepartmentDocumentCardPage)
async def list_department_structural_document_cards(
    department_id: uuid.UUID,
    db: DbSession,
    current_user: CurrentUser,
    query: str | None = None,
    document_type: str | None = None,
    document_level: str | None = None,
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
):
    await _require_agent_access(db, current_user)
    try:
        service = NdControlDepartmentDetailService(db)
        items, total = await service.list_document_cards(
            department_id,
            query=query,
            document_type=document_type,
            document_level=document_level,
            page=page,
            size=size,
        )
        return DepartmentDocumentCardPage(items=items, total=total, page=page, size=size)
    except NdControlDepartmentServiceError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.get("/departments/{department_id}/processes", response_model=DepartmentProcessPage)
async def list_department_processes(
    department_id: uuid.UUID,
    db: DbSession,
    current_user: CurrentUser,
    query: str | None = None,
    filter: str | None = Query(None, alias="filter"),
    sort: str | None = Query(None, alias="sort"),
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
):
    await _require_agent_access(db, current_user)
    try:
        service = NdControlDepartmentDetailService(db)
        items, total = await service.list_processes(
            department_id, query=query, filter_key=filter, sort_key=sort, page=page, size=size
        )
        return DepartmentProcessPage(items=items, total=total, page=page, size=size)
    except NdControlDepartmentServiceError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.get("/departments/{department_id}/relations", response_model=DepartmentRelationPage)
async def list_department_relations(
    department_id: uuid.UUID,
    db: DbSession,
    current_user: CurrentUser,
    query: str | None = None,
    filter: str | None = Query(None, alias="filter"),
    relation_type: str | None = None,
    confidence: str | None = None,
    extraction_type: str | None = None,
    process_id: uuid.UUID | None = None,
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
):
    await _require_agent_access(db, current_user)
    try:
        service = NdControlDepartmentDetailService(db)
        items, total = await service.list_relations(
            department_id,
            query=query,
            filter_key=filter,
            relation_type=relation_type,
            confidence=confidence,
            extraction_type=extraction_type,
            process_id=process_id,
            page=page,
            size=size,
        )
        return DepartmentRelationPage(items=items, total=total, page=page, size=size)
    except NdControlDepartmentServiceError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.get("/departments/{department_id}/analysis-runs", response_model=DepartmentAnalysisRunPage)
async def list_department_analysis_runs(
    department_id: uuid.UUID,
    db: DbSession,
    current_user: CurrentUser,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
):
    await _require_agent_access(db, current_user)
    try:
        service = NdControlDepartmentDetailService(db)
        items, total = await service.list_analysis_runs(department_id, page=page, size=size)
        return DepartmentAnalysisRunPage(items=items, total=total, page=page, size=size)
    except NdControlDepartmentServiceError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.get("/review/pending", response_model=DepartmentReviewPendingRead)
async def list_review_pending(
    db: DbSession,
    current_user: CurrentUser,
    department_id: uuid.UUID = Query(...),
    query: str | None = None,
    filter: str | None = Query(None, alias="filter"),
):
    await _require_agent_access(db, current_user)
    try:
        payload = await NdControlDepartmentDetailService(db).list_review_pending(
            department_id, query=query, filter_key=filter
        )
        return DepartmentReviewPendingRead.model_validate(payload)
    except NdControlDepartmentServiceError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post("/review/relations/bulk-approve", response_model=BulkApproveRelationsResponse)
async def bulk_approve_relations(
    payload: BulkApproveRelationsRequest,
    db: DbSession,
    current_user: CurrentUser,
):
    await _require_manage_access(db, current_user)
    try:
        result = await NdControlDepartmentDetailService(db).bulk_approve_relations(payload.relation_ids)
        await db.commit()
        return BulkApproveRelationsResponse.model_validate(result)
    except NdControlDepartmentDetailServiceError as exc:
        await db.rollback()
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post("/review/relations/{relation_id}/approve", status_code=status.HTTP_204_NO_CONTENT)
async def approve_relation(relation_id: uuid.UUID, db: DbSession, current_user: CurrentUser):
    await _require_manage_access(db, current_user)
    try:
        await NdControlDepartmentDetailService(db).approve_relation(relation_id)
        await db.commit()
    except NdControlDepartmentDetailServiceError as exc:
        await db.rollback()
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post("/review/relations/{relation_id}/reject", status_code=status.HTTP_204_NO_CONTENT)
async def reject_relation(relation_id: uuid.UUID, db: DbSession, current_user: CurrentUser):
    await _require_manage_access(db, current_user)
    try:
        await NdControlDepartmentDetailService(db).reject_relation(relation_id)
        await db.commit()
    except NdControlDepartmentDetailServiceError as exc:
        await db.rollback()
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post("/review/processes/{process_id}/confirm-owner", status_code=status.HTTP_204_NO_CONTENT)
async def confirm_process_owner(
    process_id: uuid.UUID,
    payload: ConfirmProcessOwnerRequest,
    db: DbSession,
    current_user: CurrentUser,
):
    await _require_manage_access(db, current_user)
    try:
        await NdControlDepartmentDetailService(db).confirm_process_owner(
            process_id, owner_name=payload.owner_name
        )
        await db.commit()
    except NdControlDepartmentDetailServiceError as exc:
        await db.rollback()
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.get("/processes/{process_id}/uml", response_model=ProcessUmlResponse)
async def get_process_uml(
    process_id: uuid.UUID,
    db: DbSession,
    current_user: CurrentUser,
    force: bool = Query(False),
    detail_level: str = Query("standard", description="compact | standard | detailed"),
):
    await _require_agent_access(db, current_user)
    try:
        result = await NdProcessUmlService(db).get_process_uml(
            process_id,
            force=force,
            detail_level=detail_level,
        )
        await db.commit()
        return ProcessUmlResponse.model_validate(result)
    except NdProcessUmlServiceError as exc:
        await db.rollback()
        if exc.code == "process_not_found":
            status_code = status.HTTP_404_NOT_FOUND
        elif exc.code == "insufficient_data":
            status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
        else:
            status_code = status.HTTP_502_BAD_GATEWAY
        if exc.code in {"empty_llm_response", "invalid_mermaid"}:
            status_code = status.HTTP_502_BAD_GATEWAY
        raise HTTPException(status_code, detail=str(exc)) from exc


@router.get("/document-cards", response_model=NdDocumentCardPage)
async def list_nd_document_cards(
    db: DbSession,
    current_user: CurrentUser,
    department_id: uuid.UUID | None = None,
    knowledge_base_id: uuid.UUID | None = None,
    query: str | None = None,
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
):
    await _require_agent_access(db, current_user)
    cards, total = await NdDocumentCardService(db).list_cards(
        department_id=department_id,
        knowledge_base_id=knowledge_base_id,
        query=query,
        page=page,
        size=size,
    )
    return NdDocumentCardPage(
        items=[NdDocumentCardRead.model_validate(card) for card in cards],
        total=total,
        page=page,
        size=size,
    )


@router.get("/document-cards/{card_id}", response_model=NdDocumentCardRead)
async def get_nd_document_card(card_id: uuid.UUID, db: DbSession, current_user: CurrentUser):
    await _require_agent_access(db, current_user)
    try:
        card = await NdDocumentCardService(db).get_card_or_raise(card_id)
        return NdDocumentCardRead.model_validate(card)
    except NdDocumentCardServiceError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.patch("/document-cards/{card_id}", response_model=NdDocumentCardRead)
async def update_nd_document_card(
    card_id: uuid.UUID,
    payload: NdDocumentCardUpdate,
    db: DbSession,
    current_user: CurrentUser,
):
    await _require_agent_access(db, current_user)
    service = NdDocumentCardService(db)
    try:
        card = await service.update_card(card_id, payload.model_dump(exclude_unset=True))
        await db.commit()
        return NdDocumentCardRead.model_validate(card)
    except NdDocumentCardServiceError as exc:
        await db.rollback()
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
