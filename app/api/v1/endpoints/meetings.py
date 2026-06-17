from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, status

from app.agents.meeting_agent.dashboard import load_login_context
from app.agents.meeting_agent.memo_presenter import build_memo_detail
from app.tools.onec.connection import CONFIG, create_session
from app.api.deps import CurrentUser, DbSession
from app.schemas.meeting import (
    MeetingInviteDraftRead,
    MeetingInvitePreviewRequest,
    MeetingInviteSendRequest,
    MeetingLoginContext,
    MeetingMemoDetailRead,
    MeetingMemoRead,
    MeetingPermissionsRead,
    MeetingRoomRead,
    MeetingRoomsRequest,
    MeetingRunCreate,
    MeetingRunRead,
    MeetingRunResultRead,
    MeetingSlotRead,
    MeetingSlotsRequest,
)
from app.services.meeting_permission import (
    can_access_meeting_agent,
    can_manage_meetings,
)
from app.services.meeting_service import MeetingService, MeetingServiceError

router = APIRouter(prefix="/meetings", tags=["meetings"])


async def _require_agent_access(db: DbSession, user: CurrentUser) -> None:
    if not await can_access_meeting_agent(db, user):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail="Нет доступа к агенту совещаний",
        )


def _service_error(status_code: int, exc: MeetingServiceError) -> HTTPException:
    return HTTPException(status_code, detail=str(exc))


@router.get("/me/permissions", response_model=MeetingPermissionsRead)
async def meeting_permissions(db: DbSession, current_user: CurrentUser) -> MeetingPermissionsRead:
    return MeetingPermissionsRead(
        can_access_agent=await can_access_meeting_agent(db, current_user),
        can_manage_meetings=await can_manage_meetings(db, current_user),
    )


@router.get("/dashboard", response_model=MeetingLoginContext)
async def get_meetings_dashboard(db: DbSession, current_user: CurrentUser) -> MeetingLoginContext:
    await _require_agent_access(db, current_user)
    context = await load_login_context(db, current_user)
    if context is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Не удалось загрузить данные dashboard",
        )
    return context


@router.get("/memos/{memo_ref_key}", response_model=MeetingMemoRead)
async def get_meeting_memo(
    memo_ref_key: uuid.UUID,
    db: DbSession,
    current_user: CurrentUser,
    memo_number: str | None = None,
) -> MeetingMemoRead:
    try:
        return await MeetingService(db).load_memo(
            current_user=current_user,
            memo_ref_key=str(memo_ref_key),
            memo_number=memo_number,
        )
    except MeetingServiceError as exc:
        raise _service_error(status.HTTP_400_BAD_REQUEST, exc) from exc


@router.get("/memos/{memo_ref_key}/detail", response_model=MeetingMemoDetailRead)
async def get_meeting_memo_detail(
    memo_ref_key: uuid.UUID,
    db: DbSession,
    current_user: CurrentUser,
) -> MeetingMemoDetailRead:
    await _require_agent_access(db, current_user)
    try:
        import asyncio

        payload = await asyncio.to_thread(
            build_memo_detail,
            create_session(CONFIG),
            CONFIG,
            str(memo_ref_key),
        )
        return MeetingMemoDetailRead.model_validate(payload)
    except Exception as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/slots", response_model=list[MeetingSlotRead])
async def find_meeting_slots(
    payload: MeetingSlotsRequest,
    db: DbSession,
    current_user: CurrentUser,
) -> list[MeetingSlotRead]:
    try:
        return await MeetingService(db).find_slots(payload, current_user=current_user)
    except MeetingServiceError as exc:
        raise _service_error(status.HTTP_400_BAD_REQUEST, exc) from exc


@router.post("/rooms", response_model=list[MeetingRoomRead])
async def find_meeting_rooms(
    payload: MeetingRoomsRequest,
    db: DbSession,
    current_user: CurrentUser,
) -> list[MeetingRoomRead]:
    try:
        return await MeetingService(db).find_rooms(payload, current_user=current_user)
    except MeetingServiceError as exc:
        raise _service_error(status.HTTP_400_BAD_REQUEST, exc) from exc


@router.post("/invite/preview", response_model=MeetingInviteDraftRead)
async def preview_meeting_invite(
    payload: MeetingInvitePreviewRequest,
    db: DbSession,
    current_user: CurrentUser,
) -> MeetingInviteDraftRead:
    try:
        return await MeetingService(db).preview_invite(payload, current_user=current_user)
    except MeetingServiceError as exc:
        raise _service_error(status.HTTP_400_BAD_REQUEST, exc) from exc


@router.post("/invite/send")
async def send_meeting_invite(
    payload: MeetingInviteSendRequest,
    db: DbSession,
    current_user: CurrentUser,
) -> dict:
    try:
        result = await MeetingService(db).send_invite(payload, current_user=current_user)
        await db.commit()
        return result
    except MeetingServiceError as exc:
        await db.rollback()
        raise _service_error(status.HTTP_400_BAD_REQUEST, exc) from exc


@router.post("/runs", response_model=MeetingRunRead, status_code=status.HTTP_201_CREATED)
async def create_meeting_run(
    payload: MeetingRunCreate,
    db: DbSession,
    current_user: CurrentUser,
) -> MeetingRunRead:
    try:
        result = await MeetingService(db).run(payload, current_user=current_user)
        await db.commit()
        return result
    except MeetingServiceError as exc:
        await db.rollback()
        raise _service_error(status.HTTP_400_BAD_REQUEST, exc) from exc


@router.get("/runs/{task_id}", response_model=MeetingRunResultRead)
async def get_meeting_run(
    task_id: uuid.UUID,
    db: DbSession,
    current_user: CurrentUser,
) -> MeetingRunResultRead:
    try:
        return await MeetingService(db).get_run(task_id, current_user=current_user)
    except MeetingServiceError as exc:
        raise _service_error(status.HTTP_404_NOT_FOUND, exc) from exc
