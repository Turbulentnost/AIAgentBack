"""OTK worker presentation REST API (quality engineer role)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from app.agents.quality_engineer_agent.otk_schemas import (
    OtkPresentationCardRead,
    OtkPresentationCreate,
    OtkPresentationListResponse,
    OtkPresentationUpdate,
    OtkShipmentLineCreate,
    OtkShipmentLineUpdate,
    OtkWriteTo1CResult,
)
from app.agents.quality_engineer_agent.otk_service import OtkPresentationService
from app.api.deps import CurrentUser, DbSession
from app.services.procurement_permission import (
    QUALITY_ENGINEER_AGENT_SLUG,
    can_access_quality_engineer,
)

router = APIRouter(
    prefix=f"/procurement/role-agents/{QUALITY_ENGINEER_AGENT_SLUG}/otk",
    tags=["otk"],
)


async def _require_otk_worker(db: DbSession, user: CurrentUser) -> None:
    if not await can_access_quality_engineer(db, user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Рабочее место доступно только инженеру по качеству / работнику ОТК",
        )


def _service(db: DbSession) -> OtkPresentationService:
    return OtkPresentationService(db=db)


@router.get("/presentations", response_model=OtkPresentationListResponse)
async def list_otk_presentations(
    db: DbSession,
    current_user: CurrentUser,
) -> OtkPresentationListResponse:
    await _require_otk_worker(db, current_user)
    return await _service(db).list_presentations()


@router.post(
    "/presentations",
    response_model=OtkPresentationCardRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_otk_presentation(
    payload: OtkPresentationCreate,
    db: DbSession,
    current_user: CurrentUser,
) -> OtkPresentationCardRead:
    await _require_otk_worker(db, current_user)
    return _service().create_presentation(payload.model_dump(mode="json"))


@router.get("/presentations/{presentation_id}", response_model=OtkPresentationCardRead)
async def get_otk_presentation(
    presentation_id: str,
    db: DbSession,
    current_user: CurrentUser,
) -> OtkPresentationCardRead:
    await _require_otk_worker(db, current_user)
    card = await _service(db).get_presentation(presentation_id)
    if card is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Предъявление не найдено")
    return card


@router.patch("/presentations/{presentation_id}", response_model=OtkPresentationCardRead)
async def update_otk_presentation(
    presentation_id: str,
    payload: OtkPresentationUpdate,
    db: DbSession,
    current_user: CurrentUser,
) -> OtkPresentationCardRead:
    await _require_otk_worker(db, current_user)
    card = await _service(db).update_presentation(presentation_id, payload)
    if card is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Предъявление не найдено")
    return card


@router.post(
    "/presentations/{presentation_id}/lines",
    response_model=OtkPresentationCardRead,
    status_code=status.HTTP_201_CREATED,
)
async def add_otk_line(
    presentation_id: str,
    payload: OtkShipmentLineCreate,
    db: DbSession,
    current_user: CurrentUser,
) -> OtkPresentationCardRead:
    await _require_otk_worker(db, current_user)
    card = await _service(db).add_line(presentation_id, payload)
    if card is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Предъявление не найдено")
    return card


@router.patch(
    "/presentations/{presentation_id}/lines/{line_id}",
    response_model=OtkPresentationCardRead,
)
async def update_otk_line(
    presentation_id: str,
    line_id: str,
    payload: OtkShipmentLineUpdate,
    db: DbSession,
    current_user: CurrentUser,
) -> OtkPresentationCardRead:
    await _require_otk_worker(db, current_user)
    card = await _service(db).update_line(presentation_id, line_id, payload)
    if card is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Предъявление или строка не найдены",
        )
    return card


@router.delete(
    "/presentations/{presentation_id}/lines/{line_id}",
    response_model=OtkPresentationCardRead,
)
async def delete_otk_line(
    presentation_id: str,
    line_id: str,
    db: DbSession,
    current_user: CurrentUser,
) -> OtkPresentationCardRead:
    await _require_otk_worker(db, current_user)
    if await _service(db).get_presentation(presentation_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Предъявление не найдено")
    card = await _service(db).delete_line(presentation_id, line_id)
    if card is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Строка не найдена")
    return card


@router.post(
    "/presentations/{presentation_id}/write-to-1c",
    response_model=OtkWriteTo1CResult,
)
async def write_otk_check_to_1c(
    presentation_id: str,
    db: DbSession,
    current_user: CurrentUser,
) -> OtkWriteTo1CResult:
    await _require_otk_worker(db, current_user)
    result = await _service(db).write_check_to_1c(presentation_id)
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Предъявление не найдено")
    return result
