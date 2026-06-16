from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession
from app.models.audit import AuditLog
from app.models.document import Document
from app.models.enums import (
    KnowledgeBaseAccessType,
    KnowledgeBaseIndexJobStatus,
    KnowledgeBaseIndexJobType,
    KnowledgeBaseStatus,
)
from app.models.knowledge_base import (
    KnowledgeBaseIndexingError as KnowledgeBaseIndexingErrorModel,
    KnowledgeBaseIndexingJob,
    KnowledgeBaseSearchQuery,
)
from app.models.user import User
from app.schemas.knowledge_base import (
    KnowledgeBaseAccessRead,
    KnowledgeBaseAccessUpdate,
    KnowledgeBaseAgentBindingInput,
    KnowledgeBaseAgentBindingRead,
    KnowledgeBaseChunkExclude,
    KnowledgeBaseChunkRead,
    KnowledgeBaseCreate,
    KnowledgeBaseListItem,
    KnowledgeBaseOverviewStats,
    KnowledgeBaseIndexCancelRequest,
    KnowledgeBaseIndexRequest,
    KnowledgeBaseIndexingErrorRead,
    KnowledgeBaseIndexingJobRead,
    KnowledgeBaseRead,
    KnowledgeBaseRuleCreate,
    KnowledgeBaseRuleRead,
    KnowledgeBaseSearchQueryCreate,
    KnowledgeBaseSearchQueryRead,
    KnowledgeBaseSourceCreate,
    KnowledgeBaseSourceRead,
    KnowledgeBaseStats,
    KnowledgeBaseTestSearchRequest,
    KnowledgeBaseTestSearchResponse,
    KnowledgeBaseUpdate,
)
from app.schemas.user import ResponsibleUserRead
from app.services.knowledge_base_access_service import KnowledgeBaseAccessService
from app.services.knowledge_base_celery import attach_celery_task_id
from app.services.knowledge_base_indexing_service import KnowledgeBaseIndexingError, KnowledgeBaseIndexingService
from app.services.knowledge_base_search_service import KnowledgeBaseSearchService
from app.services.knowledge_base_service import KnowledgeBaseService, KnowledgeBaseServiceError, file_extension
from app.services.user_service import UserService
from app.workers.tasks import (
    index_knowledge_base_full,
    index_knowledge_base_source,
    reindex_knowledge_base_embeddings,
    run_knowledge_base_search_query,
)

router = APIRouter(prefix="/knowledge-bases", tags=["knowledge-bases"])


async def _commit_and_refresh(db: DbSession, entity):
    await db.commit()
    await db.refresh(entity)
    return entity


async def _commit_and_refresh_all(db: DbSession, entities):
    await db.commit()
    for entity in entities:
        await db.refresh(entity)
    return entities


async def _enqueue_indexing_job(db: DbSession, job: KnowledgeBaseIndexingJob, async_result) -> KnowledgeBaseIndexingJob:
    job.processing_params = attach_celery_task_id(job.processing_params, async_result.id)
    return await _commit_and_refresh(db, job)


@router.get("/stats", response_model=KnowledgeBaseStats)
async def knowledge_base_stats(db: DbSession, current_user: CurrentUser):
    return await KnowledgeBaseService(db).stats(user=current_user)


@router.get("/responsible-users", response_model=list[ResponsibleUserRead])
async def list_responsible_users(db: DbSession, current_user: CurrentUser):
    _ = current_user
    users = await UserService(db).list_platform_access_users()
    return [
        ResponsibleUserRead(
            id=user.id,
            full_name=user.full_name,
            position=user.position,
            department_id=user.department_id,
            department_name=user.department.name if user.department else None,
        )
        for user in users
    ]


@router.get("", response_model=list[KnowledgeBaseListItem])
async def list_knowledge_bases(
    db: DbSession,
    current_user: CurrentUser,
    status_filter: Annotated[KnowledgeBaseStatus | None, Query(alias="status")] = None,
    department_id: uuid.UUID | None = None,
    responsible_user_id: uuid.UUID | None = None,
    query: str | None = None,
):
    items = await KnowledgeBaseService(db).list_knowledge_bases(
        status=status_filter,
        department_id=department_id,
        responsible_user_id=responsible_user_id,
        query=query,
    )
    access_service = KnowledgeBaseAccessService(db)
    service = KnowledgeBaseService(db)
    active_indexing_ids = await service.active_indexing_knowledge_base_ids([kb.id for kb in items])
    result: list[KnowledgeBaseListItem] = []
    for kb in items:
        kb_loaded = await access_service.load_for_access_check(kb.id) or kb
        read_access = await access_service.can_access_knowledge_base(
            user=current_user,
            knowledge_base=kb_loaded,
            required_access=KnowledgeBaseAccessType.READ,
        )
        search_access = await access_service.can_access_knowledge_base(
            user=current_user,
            knowledge_base=kb_loaded,
            required_access=KnowledgeBaseAccessType.SEARCH,
            allow_non_ready_for_admin=False,
        )
        admin_access = await access_service.can_access_knowledge_base(
            user=current_user,
            knowledge_base=kb_loaded,
            required_access=KnowledgeBaseAccessType.ADMIN,
        )
        indexing_active = kb.id in active_indexing_ids
        can_delete = current_user.is_superuser or kb.owner_user_id == current_user.id
        can_confirm_review = (
            kb.status == KnowledgeBaseStatus.NEEDS_REVIEW
            and not indexing_active
            and (
                can_delete
                or kb.responsible_user_id == current_user.id
                or admin_access.allowed
            )
        )
        can_manage_access = current_user.is_superuser or kb.responsible_user_id == current_user.id
        base = KnowledgeBaseRead.model_validate(kb)
        result.append(
            KnowledgeBaseListItem(
                **base.model_dump(),
                can_access=read_access.allowed,
                can_search=search_access.allowed,
                can_delete=can_delete,
                can_confirm_review=can_confirm_review,
                can_manage_access=can_manage_access,
                indexing_active=indexing_active,
            )
        )
    return result


@router.post("", response_model=KnowledgeBaseRead, status_code=status.HTTP_201_CREATED)
async def create_knowledge_base(payload: KnowledgeBaseCreate, db: DbSession, current_user: CurrentUser):
    try:
        item = await KnowledgeBaseService(db).create(payload, current_user=current_user)
        return await _commit_and_refresh(db, item)
    except KnowledgeBaseServiceError as exc:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/{knowledge_base_id}", response_model=KnowledgeBaseRead)
async def get_knowledge_base(knowledge_base_id: uuid.UUID, db: DbSession, current_user: CurrentUser):
    item = await KnowledgeBaseService(db).get(knowledge_base_id)
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="База знаний не найдена")
    return item


@router.patch("/{knowledge_base_id}", response_model=KnowledgeBaseRead)
async def update_knowledge_base(
    knowledge_base_id: uuid.UUID,
    payload: KnowledgeBaseUpdate,
    db: DbSession,
    current_user: CurrentUser,
):
    try:
        item = await KnowledgeBaseService(db).update(knowledge_base_id, payload, current_user=current_user)
        return await _commit_and_refresh(db, item)
    except KnowledgeBaseServiceError as exc:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.delete("/{knowledge_base_id}", response_model=KnowledgeBaseRead)
async def archive_knowledge_base(knowledge_base_id: uuid.UUID, db: DbSession, current_user: CurrentUser):
    try:
        item = await KnowledgeBaseService(db).archive(knowledge_base_id, current_user=current_user)
        return await _commit_and_refresh(db, item)
    except KnowledgeBaseServiceError as exc:
        await db.rollback()
        message = str(exc)
        if "может только" in message:
            status_code = status.HTTP_403_FORBIDDEN
        elif "уже удалена" in message:
            status_code = status.HTTP_409_CONFLICT
        else:
            status_code = status.HTTP_404_NOT_FOUND
        raise HTTPException(status_code=status_code, detail=message) from exc


@router.post("/{knowledge_base_id}/confirm-review", response_model=KnowledgeBaseRead)
async def confirm_knowledge_base_review(knowledge_base_id: uuid.UUID, db: DbSession, current_user: CurrentUser):
    try:
        item = await KnowledgeBaseService(db).confirm_review(knowledge_base_id, current_user=current_user)
        return await _commit_and_refresh(db, item)
    except KnowledgeBaseServiceError as exc:
        await db.rollback()
        message = str(exc)
        if "Недостаточно прав" in message:
            status_code = status.HTTP_403_FORBIDDEN
        elif "Подтверждение доступно" in message or "во время индексации" in message or "удалена" in message:
            status_code = status.HTTP_409_CONFLICT
        else:
            status_code = status.HTTP_404_NOT_FOUND
        raise HTTPException(status_code=status_code, detail=message) from exc


@router.get("/{knowledge_base_id}/sources", response_model=list[KnowledgeBaseSourceRead])
async def list_sources(knowledge_base_id: uuid.UUID, db: DbSession, current_user: CurrentUser):
    sources = await KnowledgeBaseService(db).list_sources(knowledge_base_id)
    documents = await _documents_by_id(db, [source.document_id for source in sources])
    return [_source_read(source, documents.get(source.document_id)) for source in sources]


@router.post("/{knowledge_base_id}/sources", response_model=KnowledgeBaseSourceRead, status_code=status.HTTP_201_CREATED)
async def add_source(
    knowledge_base_id: uuid.UUID,
    payload: KnowledgeBaseSourceCreate,
    db: DbSession,
    current_user: CurrentUser,
):
    try:
        source = await KnowledgeBaseService(db).add_source(knowledge_base_id, payload, current_user=current_user)
        await db.commit()
        await db.refresh(source)
        document = await db.get(Document, source.document_id)
        return _source_read(source, document)
    except KnowledgeBaseServiceError as exc:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.delete("/{knowledge_base_id}/sources/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_source(knowledge_base_id: uuid.UUID, source_id: uuid.UUID, db: DbSession, current_user: CurrentUser):
    try:
        await KnowledgeBaseService(db).remove_source(knowledge_base_id, source_id, current_user=current_user)
        await db.commit()
    except KnowledgeBaseServiceError as exc:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post("/{knowledge_base_id}/sources/{source_id}/exclude", response_model=KnowledgeBaseSourceRead)
async def exclude_source(knowledge_base_id: uuid.UUID, source_id: uuid.UUID, db: DbSession, current_user: CurrentUser):
    try:
        source = await KnowledgeBaseService(db).exclude_source(knowledge_base_id, source_id, current_user=current_user)
        await db.commit()
        await db.refresh(source)
        document = await db.get(Document, source.document_id)
        return _source_read(source, document)
    except KnowledgeBaseServiceError as exc:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.get("/{knowledge_base_id}/overview", response_model=KnowledgeBaseOverviewStats)
async def knowledge_base_overview(knowledge_base_id: uuid.UUID, db: DbSession, current_user: CurrentUser):
    _ = current_user
    assessment = await KnowledgeBaseSearchService(db).readiness_assessment(knowledge_base_id)
    return KnowledgeBaseOverviewStats(
        sources_total=assessment["sources_total"],
        sources_processed=assessment["sources_ready"],
        sources_with_errors=assessment["unresolved_errors"],
        fragments_total=assessment["fragments_total"],
        qdrant_points=assessment["fragments_total"],
        fulltext_chunks=assessment["fts_chunks"],
        quality_percent=assessment["quality_percent"],
        unresolved_errors=assessment["unresolved_errors"],
    )


@router.post("/{knowledge_base_id}/sources/{source_id}/reindex", response_model=KnowledgeBaseIndexingJobRead)
async def reindex_source(knowledge_base_id: uuid.UUID, source_id: uuid.UUID, db: DbSession, current_user: CurrentUser):
    job = await KnowledgeBaseIndexingService(db).create_job(
        knowledge_base_id,
        job_type=KnowledgeBaseIndexJobType.SOURCE,
        started_by_user_id=current_user.id,
        target_source_id=source_id,
    )
    job = await _commit_and_refresh(db, job)
    async_result = index_knowledge_base_source.delay(str(knowledge_base_id), str(source_id), str(job.id))
    return await _enqueue_indexing_job(db, job, async_result)


@router.get("/{knowledge_base_id}/chunks", response_model=list[KnowledgeBaseChunkRead])
async def list_chunks(knowledge_base_id: uuid.UUID, db: DbSession, current_user: CurrentUser):
    rows = await KnowledgeBaseService(db).list_chunks(knowledge_base_id)
    return [_chunk_read(*row) for row in rows]


@router.patch("/{knowledge_base_id}/chunks/{kb_chunk_id}/exclude", response_model=KnowledgeBaseChunkRead)
async def exclude_chunk(
    knowledge_base_id: uuid.UUID,
    kb_chunk_id: uuid.UUID,
    payload: KnowledgeBaseChunkExclude,
    db: DbSession,
    current_user: CurrentUser,
):
    try:
        kb_chunk = await KnowledgeBaseService(db).exclude_chunk(
            knowledge_base_id,
            kb_chunk_id,
            is_excluded=payload.is_excluded_from_search,
            reason=payload.exclusion_reason,
            current_user=current_user,
        )
        await db.commit()
        rows = await KnowledgeBaseService(db).list_chunks(knowledge_base_id)
        row = next((item for item in rows if item[0].id == kb_chunk.id), None)
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Фрагмент базы знаний не найден")
        return _chunk_read(*row)
    except KnowledgeBaseServiceError as exc:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.get("/{knowledge_base_id}/rules", response_model=list[KnowledgeBaseRuleRead])
async def list_rules(knowledge_base_id: uuid.UUID, db: DbSession, current_user: CurrentUser):
    return await KnowledgeBaseService(db).list_rules(knowledge_base_id)


@router.post("/{knowledge_base_id}/rules", response_model=KnowledgeBaseRuleRead, status_code=status.HTTP_201_CREATED)
async def create_rule(
    knowledge_base_id: uuid.UUID,
    payload: KnowledgeBaseRuleCreate,
    db: DbSession,
    current_user: CurrentUser,
):
    item = await KnowledgeBaseService(db).create_rule(knowledge_base_id, payload, current_user=current_user)
    return await _commit_and_refresh(db, item)


@router.get("/{knowledge_base_id}/access", response_model=KnowledgeBaseAccessRead)
async def list_access(knowledge_base_id: uuid.UUID, db: DbSession, current_user: CurrentUser):
    grants, exceptions = await KnowledgeBaseService(db).list_access(knowledge_base_id)
    return KnowledgeBaseAccessRead(grants=grants, exceptions=exceptions)


@router.put("/{knowledge_base_id}/access", response_model=KnowledgeBaseAccessRead)
async def replace_access(
    knowledge_base_id: uuid.UUID,
    payload: KnowledgeBaseAccessUpdate,
    db: DbSession,
    current_user: CurrentUser,
):
    try:
        grants, exceptions = await KnowledgeBaseService(db).replace_access(
            knowledge_base_id,
            payload,
            current_user=current_user,
        )
        await db.commit()
        return KnowledgeBaseAccessRead(grants=grants, exceptions=exceptions)
    except KnowledgeBaseServiceError as exc:
        await db.rollback()
        message = str(exc)
        if "может только ответственный" in message:
            status_code = status.HTTP_403_FORBIDDEN
        else:
            status_code = status.HTTP_400_BAD_REQUEST
        raise HTTPException(status_code=status_code, detail=message) from exc


@router.get("/{knowledge_base_id}/agents", response_model=list[KnowledgeBaseAgentBindingRead])
async def list_agents(knowledge_base_id: uuid.UUID, db: DbSession, current_user: CurrentUser):
    return await KnowledgeBaseService(db).list_agents(knowledge_base_id)


@router.put("/{knowledge_base_id}/agents", response_model=list[KnowledgeBaseAgentBindingRead])
async def replace_agents(
    knowledge_base_id: uuid.UUID,
    payload: list[KnowledgeBaseAgentBindingInput],
    db: DbSession,
    current_user: CurrentUser,
):
    items = await KnowledgeBaseService(db).replace_agents(knowledge_base_id, payload, current_user=current_user)
    return await _commit_and_refresh_all(db, items)


@router.post("/{knowledge_base_id}/index", response_model=KnowledgeBaseIndexingJobRead)
async def start_indexing(
    knowledge_base_id: uuid.UUID,
    payload: KnowledgeBaseIndexRequest,
    db: DbSession,
    current_user: CurrentUser,
):
    kb = await KnowledgeBaseService(db).get(knowledge_base_id)
    if kb is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="База знаний не найдена")

    indexing_service = KnowledgeBaseIndexingService(db)
    # Полная индексация уже идёт или ждёт в очереди — возвращаем её вместо
    # создания дубля (дубль после первой джобы сбрасывает прогресс с нуля).
    if payload.job_type in {KnowledgeBaseIndexJobType.FULL, KnowledgeBaseIndexJobType.ACCESS_REINDEX}:
        existing = await db.scalar(
            select(KnowledgeBaseIndexingJob)
            .where(
                KnowledgeBaseIndexingJob.knowledge_base_id == knowledge_base_id,
                KnowledgeBaseIndexingJob.status.in_(
                    [KnowledgeBaseIndexJobStatus.QUEUED, KnowledgeBaseIndexJobStatus.RUNNING]
                ),
                KnowledgeBaseIndexingJob.job_type.in_(
                    [
                        KnowledgeBaseIndexJobType.FULL,
                        KnowledgeBaseIndexJobType.ACCESS_REINDEX,
                        KnowledgeBaseIndexJobType.EMBEDDINGS,
                    ]
                ),
            )
            .order_by(KnowledgeBaseIndexingJob.created_at.desc())
            .limit(1)
        )
        if existing is not None:
            return existing
    await indexing_service.supersede_stale_queued_jobs(knowledge_base_id)
    await indexing_service.mark_indexing_queued(knowledge_base_id)
    job = await indexing_service.create_job(
        knowledge_base_id,
        job_type=payload.job_type,
        started_by_user_id=current_user.id,
        target_source_id=payload.source_id,
        target_chunk_id=payload.chunk_id,
    )
    job = await _commit_and_refresh(db, job)
    if payload.job_type == KnowledgeBaseIndexJobType.SOURCE and payload.source_id is not None:
        async_result = index_knowledge_base_source.delay(
            str(knowledge_base_id), str(payload.source_id), str(job.id)
        )
    elif payload.job_type == KnowledgeBaseIndexJobType.EMBEDDINGS:
        async_result = reindex_knowledge_base_embeddings.delay(str(knowledge_base_id), str(job.id))
    else:
        async_result = index_knowledge_base_full.delay(str(knowledge_base_id), str(job.id))
    return await _enqueue_indexing_job(db, job, async_result)


async def _fetch_latest_indexing_job(
    db: DbSession,
    knowledge_base_id: uuid.UUID,
) -> KnowledgeBaseIndexingJob | None:
    result = await db.execute(
        select(KnowledgeBaseIndexingJob)
        .where(KnowledgeBaseIndexingJob.knowledge_base_id == knowledge_base_id)
        .order_by(KnowledgeBaseIndexingJob.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


@router.post("/{knowledge_base_id}/index/cancel", response_model=KnowledgeBaseIndexingJobRead)
async def cancel_indexing(
    knowledge_base_id: uuid.UUID,
    payload: KnowledgeBaseIndexCancelRequest,
    db: DbSession,
    current_user: CurrentUser,
):
    indexing_service = KnowledgeBaseIndexingService(db)
    try:
        if payload.force:
            job = await indexing_service.force_cancel(
                knowledge_base_id,
                requested_by_user_id=current_user.id,
                reason=payload.reason,
            )
        else:
            job = await indexing_service.request_cancel(
                knowledge_base_id,
                requested_by_user_id=current_user.id,
                reason=payload.reason,
            )
    except KnowledgeBaseIndexingError as exc:
        await db.rollback()
        latest = await _fetch_latest_indexing_job(db, knowledge_base_id)
        if latest is not None and latest.status == KnowledgeBaseIndexJobStatus.CANCELLED:
            return latest
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    if job is not None:
        return await _commit_and_refresh(db, job)

    await db.commit()
    latest = await _fetch_latest_indexing_job(db, knowledge_base_id)
    if latest is not None:
        return latest
    raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Активное задание индексации не найдено")


@router.get("/{knowledge_base_id}/index/jobs", response_model=list[KnowledgeBaseIndexingJobRead])
async def list_jobs(knowledge_base_id: uuid.UUID, db: DbSession, current_user: CurrentUser):
    return await KnowledgeBaseService(db).list_jobs(knowledge_base_id)


@router.get("/index/jobs/{job_id}/errors", response_model=list[KnowledgeBaseIndexingErrorRead])
async def list_job_errors(job_id: uuid.UUID, db: DbSession, current_user: CurrentUser):
    return await KnowledgeBaseService(db).list_job_errors(job_id)


@router.post("/index/errors/{error_id}/retry", response_model=KnowledgeBaseIndexingJobRead)
async def retry_indexing_error(error_id: uuid.UUID, db: DbSession, current_user: CurrentUser):
    error = await db.get(KnowledgeBaseIndexingErrorModel, error_id)
    if error is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ошибка индексации не найдена")
    job = await KnowledgeBaseIndexingService(db).create_job(
        error.knowledge_base_id,
        job_type=KnowledgeBaseIndexJobType.SOURCE if error.source_id else KnowledgeBaseIndexJobType.FULL,
        started_by_user_id=current_user.id,
        target_source_id=error.source_id,
    )
    error.is_resolved = True
    await db.commit()
    if error.source_id:
        async_result = index_knowledge_base_source.delay(
            str(error.knowledge_base_id), str(error.source_id), str(job.id)
        )
    else:
        async_result = index_knowledge_base_full.delay(str(error.knowledge_base_id), str(job.id))
    job.processing_params = attach_celery_task_id(job.processing_params, async_result.id)
    await db.commit()
    await db.refresh(job)
    return job


@router.get("/{knowledge_base_id}/readiness")
async def knowledge_base_readiness(knowledge_base_id: uuid.UUID, db: DbSession, current_user: CurrentUser):
    _ = current_user
    return await KnowledgeBaseSearchService(db).readiness_assessment(knowledge_base_id)


@router.post("/{knowledge_base_id}/test-search", response_model=KnowledgeBaseTestSearchResponse)
async def test_search(
    knowledge_base_id: uuid.UUID,
    payload: KnowledgeBaseTestSearchRequest,
    db: DbSession,
    current_user: CurrentUser,
):
    service = KnowledgeBaseService(db)
    access_service = KnowledgeBaseAccessService(db)
    kb = await access_service.load_for_access_check(knowledge_base_id)
    if kb is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="База знаний не найдена")
    if kb.status == KnowledgeBaseStatus.DRAFT:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Тест поиска недоступен: база ещё не проиндексирована. Добавьте источники и запустите индексацию.",
        )
    if kb.status == KnowledgeBaseStatus.PROCESSING and kb.fragments_count == 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Тест поиска временно недоступен: идёт первая индексация базы знаний.",
        )
    search_access = await access_service.can_access_knowledge_base(
        user=current_user,
        knowledge_base=kb,
        required_access=KnowledgeBaseAccessType.SEARCH,
        allow_non_ready_for_admin=True,
    )
    if not search_access.allowed:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Недостаточно прав для поиска по базе знаний")
    acting_user = await _acting_user(db, current_user, payload)
    result = await KnowledgeBaseSearchService(db).search(
        knowledge_base=kb,
        query=payload.query,
        user=acting_user,
        top_k=payload.top_k,
        agent_id=payload.agent_id,
        include_inaccessible=True,
        test_mode=True,
        viewer=current_user,
    )
    await service.audit.log(
        action="kb.test_search",
        actor_id=current_user.id,
        resource_type="knowledge_base",
        resource_id=str(kb.id),
        payload={
            "query": payload.query,
            "acting_user_id": str(getattr(acting_user, "id", current_user.id)),
            "department_id": str(getattr(acting_user, "department_id", "")) if getattr(acting_user, "department_id", None) else None,
            "agent_id": str(payload.agent_id) if payload.agent_id else None,
            "hits": len(result.hits),
        },
    )
    await db.commit()
    return result


async def _check_search_access(db: DbSession, knowledge_base_id: uuid.UUID, current_user: User):
    access_service = KnowledgeBaseAccessService(db)
    kb = await access_service.load_for_access_check(knowledge_base_id)
    if kb is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="База знаний не найдена")
    search_access = await access_service.can_access_knowledge_base(
        user=current_user,
        knowledge_base=kb,
        required_access=KnowledgeBaseAccessType.SEARCH,
        allow_non_ready_for_admin=True,
    )
    if not search_access.allowed:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Недостаточно прав для поиска по базе знаний")
    return kb


@router.post(
    "/{knowledge_base_id}/search-queries",
    response_model=KnowledgeBaseSearchQueryRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_search_query(
    knowledge_base_id: uuid.UUID,
    payload: KnowledgeBaseSearchQueryCreate,
    db: DbSession,
    current_user: CurrentUser,
):
    kb = await _check_search_access(db, knowledge_base_id, current_user)
    if kb.status == KnowledgeBaseStatus.DRAFT:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Поиск недоступен: база ещё не проиндексирована.",
        )
    item = KnowledgeBaseSearchQuery(
        knowledge_base_id=knowledge_base_id,
        user_id=current_user.id,
        query=payload.query.strip(),
        top_k=max(1, min(payload.top_k, 50)),
        status="pending",
    )
    db.add(item)
    await db.commit()
    await db.refresh(item)
    run_knowledge_base_search_query.delay(str(item.id))
    return item


@router.get(
    "/{knowledge_base_id}/search-queries",
    response_model=list[KnowledgeBaseSearchQueryRead],
)
async def list_search_queries(
    knowledge_base_id: uuid.UUID,
    db: DbSession,
    current_user: CurrentUser,
    limit: int = 30,
):
    await _check_search_access(db, knowledge_base_id, current_user)
    result = await db.execute(
        select(KnowledgeBaseSearchQuery)
        .where(
            KnowledgeBaseSearchQuery.knowledge_base_id == knowledge_base_id,
            KnowledgeBaseSearchQuery.user_id == current_user.id,
        )
        .order_by(KnowledgeBaseSearchQuery.created_at.asc())
        .limit(max(1, min(limit, 100)))
    )
    return list(result.scalars().all())


@router.get(
    "/{knowledge_base_id}/search-queries/{search_query_id}",
    response_model=KnowledgeBaseSearchQueryRead,
)
async def get_search_query(
    knowledge_base_id: uuid.UUID,
    search_query_id: uuid.UUID,
    db: DbSession,
    current_user: CurrentUser,
):
    item = await db.get(KnowledgeBaseSearchQuery, search_query_id)
    if item is None or item.knowledge_base_id != knowledge_base_id or item.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Запрос не найден")
    return item


@router.post(
    "/{knowledge_base_id}/search-queries/{search_query_id}/cancel",
    response_model=KnowledgeBaseSearchQueryRead,
)
async def cancel_search_query(
    knowledge_base_id: uuid.UUID,
    search_query_id: uuid.UUID,
    db: DbSession,
    current_user: CurrentUser,
):
    item = await db.get(KnowledgeBaseSearchQuery, search_query_id)
    if item is None or item.knowledge_base_id != knowledge_base_id or item.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Запрос не найден")
    if item.status in {"pending", "running"}:
        item.cancel_requested = True
        if item.status == "pending":
            item.status = "cancelled"
            item.finished_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(item)
    return item


@router.get("/{knowledge_base_id}/audit")
async def list_audit(knowledge_base_id: uuid.UUID, db: DbSession, current_user: CurrentUser):
    items = await KnowledgeBaseService(db).list_audit(knowledge_base_id)
    return [_audit_read(item) for item in items]


async def _acting_user(db: DbSession, current_user: User, payload: KnowledgeBaseTestSearchRequest):
    if payload.user_id is not None:
        if not current_user.is_superuser and payload.user_id != current_user.id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Недостаточно прав для проверки от имени пользователя")
        user = await db.get(User, payload.user_id)
        if user is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Пользователь для проверки не найден")
        return user
    if payload.department_id is not None:
        if not current_user.is_superuser:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Недостаточно прав для проверки подразделения")
        return SimpleNamespace(id=current_user.id, department_id=payload.department_id, is_superuser=False)
    return current_user


async def _documents_by_id(db: DbSession, document_ids: list[uuid.UUID]) -> dict[uuid.UUID, Document]:
    if not document_ids:
        return {}
    result = await db.execute(select(Document).where(Document.id.in_(document_ids)))
    return {document.id: document for document in result.scalars().all()}


def _document_relative_path(document: Document | None) -> str | None:
    if not document:
        return None
    for payload in (document.metadata_, document.doc_metadata):
        if isinstance(payload, dict):
            relative_path = payload.get("relative_path")
            if relative_path:
                return str(relative_path).replace("\\", "/")
    if document.original_filename:
        return document.original_filename
    return document.title


def _source_read(source, document: Document | None) -> dict:
    filename = document.original_filename if document else None
    return {
        "id": source.id,
        "knowledge_base_id": source.knowledge_base_id,
        "document_id": source.document_id,
        "document_version_id": source.document_version_id,
        "added_by_user_id": source.added_by_user_id,
        "added_at": source.added_at,
        "processing_status": source.processing_status,
        "last_indexed_at": source.last_indexed_at,
        "fragments_count": source.fragments_count,
        "file_size": source.file_size,
        "access_snapshot": source.access_snapshot,
        "precheck_status": getattr(source, "precheck_status", None),
        "precheck_notes": getattr(source, "precheck_notes", None),
        "checksum": getattr(source, "checksum", None),
        "quality_status": getattr(source, "quality_status", None),
        "pages_count": getattr(source, "pages_count", None),
        "created_at": source.created_at,
        "updated_at": source.updated_at,
        "document_title": document.title if document else None,
        "original_filename": filename,
        "extension": file_extension(filename),
        "relative_path": _document_relative_path(document),
        "department_id": document.department_id if document else None,
        "linked_agents_count": 0,
    }


def _chunk_read(kb_chunk, document_chunk, document: Document | None) -> dict:
    return {
        "id": kb_chunk.id,
        "knowledge_base_id": kb_chunk.knowledge_base_id,
        "source_id": kb_chunk.source_id,
        "document_chunk_id": kb_chunk.document_chunk_id,
        "is_excluded_from_search": kb_chunk.is_excluded_from_search,
        "exclusion_reason": kb_chunk.exclusion_reason,
        "indexed_at": kb_chunk.indexed_at,
        "embedding_status": kb_chunk.embedding_status,
        "quality_status": getattr(kb_chunk, "quality_status", None),
        "clause_number": kb_chunk.clause_number,
        "fragment_type": kb_chunk.fragment_type,
        "access_snapshot": kb_chunk.access_snapshot,
        "text": document_chunk.content or document_chunk.text,
        "metadata": document_chunk.metadata_ or document_chunk.chunk_metadata,
        "chunk_index": document_chunk.chunk_index,
        "document_id": document.id if document else document_chunk.document_id,
        "document_title": document.title if document else None,
        "page_number": document_chunk.page_number,
        "section_title": document_chunk.section_title,
    }


def _audit_read(item: AuditLog) -> dict:
    return {
        "id": item.id,
        "actor_type": item.actor_type,
        "actor_id": item.actor_id,
        "action": item.action,
        "resource_type": item.resource_type,
        "resource_id": item.resource_id,
        "payload": item.payload,
        "ip_address": item.ip_address,
        "user_agent": item.user_agent,
        "note": item.note,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }
