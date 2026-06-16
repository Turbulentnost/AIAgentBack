from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Query, status

from app.api.deps import CurrentUser, DbSession
from app.schemas.nd_control_registry import (
    NdControlDepartmentCreate,
    NdControlDepartmentKnowledgeBasesUpdate,
    NdControlDepartmentRead,
    NdControlDepartmentUpdate,
    NdControlPermissionsRead,
    NdDocumentCardPage,
    NdDocumentCardRead,
    NdDocumentCardUpdate,
)
from app.services.nd_control_department_service import (
    NdControlDepartmentService,
    NdControlDepartmentServiceError,
)
from app.services.nd_control_permission import (
    can_access_nd_control_agent,
    can_manage_nd_control_departments,
)
from app.services.nd_document_card_service import NdDocumentCardService, NdDocumentCardServiceError

router = APIRouter(prefix="/nd-control", tags=["nd-control"])


async def _require_agent_access(db: DbSession, user: CurrentUser) -> None:
    if not await can_access_nd_control_agent(db, user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Нет доступа к агенту контроля НД")


async def _require_manage_access(db: DbSession, user: CurrentUser) -> None:
    if not await can_manage_nd_control_departments(db, user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Недостаточно прав для управления отделами")


def _department_read(item: dict) -> NdControlDepartmentRead:
    dept = item["department"]
    base = NdControlDepartmentRead.model_validate(dept)
    return base.model_copy(
        update={
            "knowledge_bases_count": item["knowledge_bases_count"],
            "cards_count": item["cards_count"],
            "knowledge_base_ids": item["knowledge_base_ids"],
        }
    )


@router.get("/me/permissions", response_model=NdControlPermissionsRead)
async def nd_control_permissions(db: DbSession, current_user: CurrentUser):
    return NdControlPermissionsRead(
        can_manage_departments=await can_manage_nd_control_departments(db, current_user),
        can_access_agent=await can_access_nd_control_agent(db, current_user),
    )


@router.get("/departments", response_model=list[NdControlDepartmentRead])
async def list_nd_control_departments(db: DbSession, current_user: CurrentUser):
    await _require_agent_access(db, current_user)
    items = await NdControlDepartmentService(db).list_departments()
    return [_department_read(item) for item in items]


@router.post("/departments", response_model=NdControlDepartmentRead, status_code=status.HTTP_201_CREATED)
async def create_nd_control_department(
    payload: NdControlDepartmentCreate,
    db: DbSession,
    current_user: CurrentUser,
):
    await _require_agent_access(db, current_user)
    service = NdControlDepartmentService(db)
    try:
        dept = await service.create_department(
            name=payload.name,
            description=payload.description,
            knowledge_base_ids=payload.knowledge_base_ids,
            current_user=current_user,
        )
        await db.commit()
        items = await service.list_departments(active_only=True)
        match = next((item for item in items if item["department"].id == dept.id), None)
        if match is None:
            raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Отдел создан, но не найден")
        return _department_read(match)
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
):
    await _require_manage_access(db, current_user)
    service = NdControlDepartmentService(db)
    try:
        await service.set_department_knowledge_bases(
            department_id,
            payload.knowledge_base_ids,
            current_user=current_user,
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
