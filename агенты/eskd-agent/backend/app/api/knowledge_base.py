from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.api.eskd_deps import OptionalEskdActor, get_optional_eskd_actor
from app.schemas.knowledge_base import (
    KnowledgeBaseDeleteResponse,
    KnowledgeBaseItemRead,
    KnowledgeBaseListResponse,
    KnowledgeBaseVerifyRequest,
    KnowledgeBaseVerifyResponse,
)
from app.services.knowledge_base_service import KnowledgeBaseService

router = APIRouter(prefix="/api/v1/eskd/knowledge-base", tags=["eskd-knowledge-base"])


@router.get("", response_model=KnowledgeBaseListResponse)
async def list_knowledge_base(
    db: AsyncSession = Depends(get_db),
    q: str | None = Query(default=None, description="Поиск по имени файла или обозначению"),
    checked: bool | None = Query(default=None, description="true — только проверенные, false — не проверенные"),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=24, ge=1, le=100),
):
    items, total, checked_count, unchecked_count = await KnowledgeBaseService(db).list_entries(
        q=q,
        checked=checked,
        page=page,
        size=size,
    )
    return KnowledgeBaseListResponse(
        items=[KnowledgeBaseItemRead(**item) for item in items],
        total=total,
        page=page,
        size=size,
        checked_count=checked_count,
        unchecked_count=unchecked_count,
    )


@router.post("/verify", response_model=KnowledgeBaseVerifyResponse)
async def verify_knowledge_base_entry(
    payload: KnowledgeBaseVerifyRequest,
    db: AsyncSession = Depends(get_db),
    actor_ctx: OptionalEskdActor = Depends(get_optional_eskd_actor),
):
    try:
        item = await KnowledgeBaseService(db).verify_entry(
            check_run_id=payload.check_run_id,
            marking_document_id=payload.marking_document_id,
            actor=actor_ctx.actor,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return KnowledgeBaseVerifyResponse(item=KnowledgeBaseItemRead(**item))


@router.delete("/{key:path}", response_model=KnowledgeBaseDeleteResponse)
async def delete_knowledge_base_entry(
    key: str,
    db: AsyncSession = Depends(get_db),
):
    try:
        result = await KnowledgeBaseService(db).delete_entry(key)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    return KnowledgeBaseDeleteResponse(**result)
