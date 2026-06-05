from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession
from app.models.audit import AuditLog
from app.models.document import Document
from app.models.enums import KnowledgeBaseIndexJobType, KnowledgeBaseStatus
from app.models.knowledge_base import KnowledgeBaseIndexingError
from app.models.user import User
from app.schemas.knowledge_base import (
    KnowledgeBaseAccessRead,
    KnowledgeBaseAccessUpdate,
    KnowledgeBaseAgentBindingInput,
    KnowledgeBaseAgentBindingRead,
    KnowledgeBaseChunkExclude,
    KnowledgeBaseChunkRead,
    KnowledgeBaseCreate,
    KnowledgeBaseIndexRequest,
    KnowledgeBaseIndexingErrorRead,
    KnowledgeBaseIndexingJobRead,
    KnowledgeBaseRead,
    KnowledgeBaseRuleCreate,
    KnowledgeBaseRuleRead,
    KnowledgeBaseSourceCreate,
    KnowledgeBaseSourceRead,
    KnowledgeBaseStats,
    KnowledgeBaseTestSearchRequest,
    KnowledgeBaseTestSearchResponse,
    KnowledgeBaseUpdate,
)
from app.services.knowledge_base_indexing_service import KnowledgeBaseIndexingService
from app.services.knowledge_base_search_service import KnowledgeBaseSearchService
from app.services.knowledge_base_service import KnowledgeBaseService, KnowledgeBaseServiceError, file_extension
from app.workers.tasks import (
    index_knowledge_base_full,
    index_knowledge_base_source,
    reindex_knowledge_base_after_access_change,
    reindex_knowledge_base_embeddings,
)

router = APIRouter(prefix="/knowledge-bases", tags=["knowledge-bases"])


@router.get("/stats", response_model=KnowledgeBaseStats)
async def knowledge_base_stats(db: DbSession, current_user: CurrentUser):
    return await KnowledgeBaseService(db).stats()


@router.get("", response_model=list[KnowledgeBaseRead])
async def list_knowledge_bases(
    db: DbSession,
    current_user: CurrentUser,
    status_filter: Annotated[KnowledgeBaseStatus | None, Query(alias="status")] = None,
    department_id: uuid.UUID | None = None,
    responsible_user_id: uuid.UUID | None = None,
    query: str | None = None,
):
    return await KnowledgeBaseService(db).list_knowledge_bases(
        status=status_filter,
        department_id=department_id,
        responsible_user_id=responsible_user_id,
        query=query,
    )


@router.post("", response_model=KnowledgeBaseRead, status_code=status.HTTP_201_CREATED)
async def create_knowledge_base(payload: KnowledgeBaseCreate, db: DbSession, current_user: CurrentUser):
    try:
        item = await KnowledgeBaseService(db).create(payload, current_user=current_user)
        await db.commit()
        return item
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
        await db.commit()
        return item
    except KnowledgeBaseServiceError as exc:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.delete("/{knowledge_base_id}", response_model=KnowledgeBaseRead)
async def archive_knowledge_base(knowledge_base_id: uuid.UUID, db: DbSession, current_user: CurrentUser):
    try:
        item = await KnowledgeBaseService(db).archive(knowledge_base_id, current_user=current_user)
        await db.commit()
        return item
    except KnowledgeBaseServiceError as exc:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


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


@router.post("/{knowledge_base_id}/sources/{source_id}/reindex", response_model=KnowledgeBaseIndexingJobRead)
async def reindex_source(knowledge_base_id: uuid.UUID, source_id: uuid.UUID, db: DbSession, current_user: CurrentUser):
    job = await KnowledgeBaseIndexingService(db).create_job(
        knowledge_base_id,
        job_type=KnowledgeBaseIndexJobType.SOURCE,
        started_by_user_id=current_user.id,
        target_source_id=source_id,
    )
    await db.commit()
    index_knowledge_base_source.delay(str(knowledge_base_id), str(source_id), str(job.id))
    return job


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
    await db.commit()
    return item


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
        job = await KnowledgeBaseIndexingService(db).create_job(
            knowledge_base_id,
            job_type=KnowledgeBaseIndexJobType.ACCESS_REINDEX,
            started_by_user_id=current_user.id,
        )
        await db.commit()
        reindex_knowledge_base_after_access_change.delay(str(knowledge_base_id), str(job.id))
        return KnowledgeBaseAccessRead(grants=grants, exceptions=exceptions)
    except KnowledgeBaseServiceError as exc:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


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
    await db.commit()
    return items


@router.post("/{knowledge_base_id}/index", response_model=KnowledgeBaseIndexingJobRead)
async def start_indexing(
    knowledge_base_id: uuid.UUID,
    payload: KnowledgeBaseIndexRequest,
    db: DbSession,
    current_user: CurrentUser,
):
    job = await KnowledgeBaseIndexingService(db).create_job(
        knowledge_base_id,
        job_type=payload.job_type,
        started_by_user_id=current_user.id,
        target_source_id=payload.source_id,
        target_chunk_id=payload.chunk_id,
    )
    await db.commit()
    if payload.job_type == KnowledgeBaseIndexJobType.SOURCE and payload.source_id is not None:
        index_knowledge_base_source.delay(str(knowledge_base_id), str(payload.source_id), str(job.id))
    elif payload.job_type == KnowledgeBaseIndexJobType.EMBEDDINGS:
        reindex_knowledge_base_embeddings.delay(str(knowledge_base_id), str(job.id))
    else:
        index_knowledge_base_full.delay(str(knowledge_base_id), str(job.id))
    return job


@router.get("/{knowledge_base_id}/index/jobs", response_model=list[KnowledgeBaseIndexingJobRead])
async def list_jobs(knowledge_base_id: uuid.UUID, db: DbSession, current_user: CurrentUser):
    return await KnowledgeBaseService(db).list_jobs(knowledge_base_id)


@router.get("/index/jobs/{job_id}/errors", response_model=list[KnowledgeBaseIndexingErrorRead])
async def list_job_errors(job_id: uuid.UUID, db: DbSession, current_user: CurrentUser):
    return await KnowledgeBaseService(db).list_job_errors(job_id)


@router.post("/index/errors/{error_id}/retry", response_model=KnowledgeBaseIndexingJobRead)
async def retry_indexing_error(error_id: uuid.UUID, db: DbSession, current_user: CurrentUser):
    error = await db.get(KnowledgeBaseIndexingError, error_id)
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
        index_knowledge_base_source.delay(str(error.knowledge_base_id), str(error.source_id), str(job.id))
    else:
        index_knowledge_base_full.delay(str(error.knowledge_base_id), str(job.id))
    return job


@router.post("/{knowledge_base_id}/test-search", response_model=KnowledgeBaseTestSearchResponse)
async def test_search(
    knowledge_base_id: uuid.UUID,
    payload: KnowledgeBaseTestSearchRequest,
    db: DbSession,
    current_user: CurrentUser,
):
    service = KnowledgeBaseService(db)
    kb = await service.get(knowledge_base_id)
    if kb is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="База знаний не найдена")
    acting_user = await _acting_user(db, current_user, payload)
    result = await KnowledgeBaseSearchService(db).search(
        knowledge_base=kb,
        query=payload.query,
        user=acting_user,
        top_k=payload.top_k,
        agent_id=payload.agent_id,
        include_inaccessible=True,
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
        "created_at": source.created_at,
        "updated_at": source.updated_at,
        "document_title": document.title if document else None,
        "original_filename": filename,
        "extension": file_extension(filename),
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
        "clause_number": kb_chunk.clause_number,
        "fragment_type": kb_chunk.fragment_type,
        "access_snapshot": kb_chunk.access_snapshot,
        "text": document_chunk.text or document_chunk.content,
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
