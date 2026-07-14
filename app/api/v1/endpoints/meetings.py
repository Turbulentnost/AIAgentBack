from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, status

from app.agents.meeting_agent.dashboard import load_login_context
from app.services.meeting_memo_cache import MeetingMemoCacheService, MemoCacheMissError
from app.api.deps import CurrentUser, DbSession
from app.schemas.meeting import (
    MeetingAgentSlotApproveRead,
    MeetingAgentSlotApproveRequest,
    MeetingAgentSlotDetailRead,
    MeetingAgentSlotDetailRequest,
    MeetingAgentSlotPreviewRead,
    MeetingAgentSlotPreviewRequest,
    MeetingInviteDraftRead,
    MeetingInvitePreviewRequest,
    MeetingInviteSendRequest,
    MeetingLoginContext,
    MeetingMemoDetailRead,
    MeetingMemoRead,
    MeetingMemoApproveRead,
    MeetingMemoApproveRequest,
    MeetingMemoRejectRead,
    MeetingMemoRejectRequest,
    MeetingPermissionsRead,
    MeetingRegistryRead,
    MeetingRegistryCancelRead,
    MeetingRegistryCancelRequest,
    MeetingRegistryHistoryRead,
    MeetingRegistryParticipantsRead,
    MeetingRegistryParticipantSearchRead,
    MeetingRegistryParticipantsApplyRead,
    MeetingRegistryParticipantsApplyRequest,
    MeetingRegistryParticipantsAddConfirmRead,
    MeetingRegistryParticipantsAddConfirmRequest,
    MeetingRegistryParticipantsRemovalConfirmRead,
    MeetingRegistryParticipantsRemovalConfirmRequest,
    MeetingRegistryRescheduleApproveRead,
    MeetingRegistryRescheduleApproveRequest,
    MeetingRegistryRescheduleSlotPreviewRead,
    MeetingRegistryRescheduleSlotPreviewRequest,
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
from app.services.meeting_exceptions import MeetingServiceError
from app.services.meeting_service import MeetingService

router = APIRouter(prefix="/meetings", tags=["meetings"])


async def _load_dashboard_context(
    db: DbSession,
    current_user: CurrentUser,
    *,
    force_refresh: bool,
) -> MeetingLoginContext:
    await _require_agent_access(db, current_user)
    context = await load_login_context(db, current_user, force_refresh=force_refresh)
    if context is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Не удалось загрузить данные dashboard",
        )
    return context


async def _require_agent_access(db: DbSession, user: CurrentUser) -> None:
    if not await can_access_meeting_agent(db, user):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail="Нет доступа к агенту совещаний",
        )


def _service_error(exc: MeetingServiceError, *, status_code: int | None = None) -> HTTPException:
    code = status_code or getattr(exc, "status_code", 400)
    return HTTPException(code, detail=str(exc))


@router.get("/me/permissions", response_model=MeetingPermissionsRead)
async def meeting_permissions(db: DbSession, current_user: CurrentUser) -> MeetingPermissionsRead:
    return MeetingPermissionsRead(
        can_access_agent=await can_access_meeting_agent(db, current_user),
        can_manage_meetings=await can_manage_meetings(db, current_user),
    )


@router.get("/dashboard", response_model=MeetingLoginContext)
async def get_meetings_dashboard(db: DbSession, current_user: CurrentUser) -> MeetingLoginContext:
    """Загрузка очереди: Redis-кэш (или 1С, если кэша ещё нет). Не обновляет данные из 1С принудительно."""
    return await _load_dashboard_context(db, current_user, force_refresh=False)


@router.post("/dashboard/refresh", response_model=MeetingLoginContext)
async def refresh_meetings_dashboard(db: DbSession, current_user: CurrentUser) -> MeetingLoginContext:
    """Принудительное обновление из 1С — только для кнопки «Обновить», не для F5/перезагрузки страницы."""
    return await _load_dashboard_context(db, current_user, force_refresh=True)


@router.get("/registry", response_model=MeetingRegistryRead)
async def get_meetings_registry(
    db: DbSession,
    current_user: CurrentUser,
    stage: str | None = None,
) -> MeetingRegistryRead:
    """Реестр совещаний: СЗ с отправленными приглашениями и этапами исполнения."""
    try:
        return await MeetingService(db).list_registry(stage=stage, current_user=current_user)
    except MeetingServiceError as exc:
        raise _service_error(exc) from exc


@router.get(
    "/registry/{memo_ref_key}/participants",
    response_model=MeetingRegistryParticipantsRead,
)
async def get_registry_meeting_participants(
    memo_ref_key: uuid.UUID,
    db: DbSession,
    current_user: CurrentUser,
) -> MeetingRegistryParticipantsRead:
    """Участники совещания из колонки participants в реестре (без 1С)."""
    await _require_agent_access(db, current_user)
    try:
        return await MeetingService(db).get_registry_participants(
            str(memo_ref_key),
            current_user=current_user,
        )
    except MeetingServiceError as exc:
        raise _service_error(exc) from exc


@router.get(
    "/registry/{memo_ref_key}/participants/search",
    response_model=MeetingRegistryParticipantSearchRead,
)
async def search_registry_meeting_participant(
    memo_ref_key: uuid.UUID,
    fio: str,
    db: DbSession,
    current_user: CurrentUser,
) -> MeetingRegistryParticipantSearchRead:
    """Поиск участника по ФИО в Outlook (Exchange GAL) для кнопки «Добавить»."""
    await _require_agent_access(db, current_user)
    try:
        return await MeetingService(db).search_registry_participant(
            str(memo_ref_key),
            fio,
            current_user=current_user,
        )
    except MeetingServiceError as exc:
        raise _service_error(exc) from exc


@router.get(
    "/registry/{memo_ref_key}/history",
    response_model=MeetingRegistryHistoryRead,
)
async def get_registry_meeting_history(
    memo_ref_key: uuid.UUID,
    db: DbSession,
    current_user: CurrentUser,
) -> MeetingRegistryHistoryRead:
    """История изменений совещания в реестре."""
    await _require_agent_access(db, current_user)
    try:
        return await MeetingService(db).get_registry_history(
            str(memo_ref_key),
            current_user=current_user,
        )
    except MeetingServiceError as exc:
        raise _service_error(exc) from exc


@router.post(
    "/registry/{memo_ref_key}/participants/apply",
    response_model=MeetingRegistryParticipantsApplyRead,
)
async def apply_registry_meeting_participants(
    memo_ref_key: uuid.UUID,
    db: DbSession,
    current_user: CurrentUser,
    payload: MeetingRegistryParticipantsApplyRequest,
) -> MeetingRegistryParticipantsApplyRead:
    """Применить изменения состава участников совещания (Outlook + реестр)."""
    await _require_agent_access(db, current_user)
    try:
        result = await MeetingService(db).apply_registry_participants(
            str(memo_ref_key),
            payload,
            current_user=current_user,
        )
        await db.commit()
        return result
    except MeetingServiceError as exc:
        await db.rollback()
        raise _service_error(exc) from exc


@router.post(
    "/registry/{memo_ref_key}/participants/confirm-add",
    response_model=MeetingRegistryParticipantsAddConfirmRead,
)
async def confirm_registry_participants_add(
    memo_ref_key: uuid.UUID,
    db: DbSession,
    current_user: CurrentUser,
    payload: MeetingRegistryParticipantsAddConfirmRequest,
) -> MeetingRegistryParticipantsAddConfirmRead:
    """Подтвердить добавление участников в текущий или новый слот (Outlook + реестр)."""
    await _require_agent_access(db, current_user)
    try:
        result = await MeetingService(db).confirm_registry_participants_add(
            str(memo_ref_key),
            payload,
            current_user=current_user,
        )
        await db.commit()
        return result
    except MeetingServiceError as exc:
        await db.rollback()
        raise _service_error(exc) from exc


@router.post(
    "/registry/{memo_ref_key}/participants/confirm-removal",
    response_model=MeetingRegistryParticipantsRemovalConfirmRead,
)
async def confirm_registry_participants_removal(
    memo_ref_key: uuid.UUID,
    db: DbSession,
    current_user: CurrentUser,
    payload: MeetingRegistryParticipantsRemovalConfirmRequest,
) -> MeetingRegistryParticipantsRemovalConfirmRead:
    """Подтвердить удаление участников с переносом на выбранный слот (Outlook + реестр)."""
    await _require_agent_access(db, current_user)
    try:
        result = await MeetingService(db).confirm_registry_participants_removal(
            str(memo_ref_key),
            payload,
            current_user=current_user,
        )
        await db.commit()
        return result
    except MeetingServiceError as exc:
        await db.rollback()
        raise _service_error(exc) from exc


@router.post(
    "/registry/{memo_ref_key}/participants/cancel-removal",
    response_model=MeetingRegistryParticipantsRead,
)
async def cancel_registry_participants_removal(
    memo_ref_key: uuid.UUID,
    db: DbSession,
    current_user: CurrentUser,
) -> MeetingRegistryParticipantsRead:
    """Отменить ожидание подтверждения удаления участников (сброс pending_removal)."""
    await _require_agent_access(db, current_user)
    try:
        result = await MeetingService(db).cancel_registry_participants_removal(
            str(memo_ref_key),
            current_user=current_user,
        )
        await db.commit()
        return result
    except MeetingServiceError as exc:
        await db.rollback()
        raise _service_error(exc) from exc


@router.post("/registry/{memo_ref_key}/cancel", response_model=MeetingRegistryCancelRead)
async def cancel_registry_meeting(
    memo_ref_key: uuid.UUID,
    db: DbSession,
    current_user: CurrentUser,
    payload: MeetingRegistryCancelRequest | None = None,
) -> MeetingRegistryCancelRead:
    """Отменить совещание в календаре и перевести запись реестра в статус «Отменено»."""
    await _require_agent_access(db, current_user)
    try:
        result = await MeetingService(db).cancel_registry_meeting(
            str(memo_ref_key),
            payload or MeetingRegistryCancelRequest(),
            current_user=current_user,
        )
        await db.commit()
        return result
    except MeetingServiceError as exc:
        await db.rollback()
        raise _service_error(exc) from exc


@router.post(
    "/registry/{memo_ref_key}/reschedule/slot-preview",
    response_model=MeetingRegistryRescheduleSlotPreviewRead,
)
async def preview_registry_reschedule_slot(
    memo_ref_key: uuid.UUID,
    db: DbSession,
    current_user: CurrentUser,
    payload: MeetingRegistryRescheduleSlotPreviewRequest | None = None,
) -> MeetingRegistryRescheduleSlotPreviewRead:
    """Ближайший свободный слот после текущего времени совещания в реестре."""
    await _require_agent_access(db, current_user)
    try:
        return await MeetingService(db).suggest_registry_reschedule_slot(
            str(memo_ref_key),
            payload or MeetingRegistryRescheduleSlotPreviewRequest(),
            current_user=current_user,
        )
    except MeetingServiceError as exc:
        raise _service_error(exc) from exc


@router.post(
    "/registry/{memo_ref_key}/reschedule/approve",
    response_model=MeetingRegistryRescheduleApproveRead,
)
async def approve_registry_reschedule(
    memo_ref_key: uuid.UUID,
    payload: MeetingRegistryRescheduleApproveRequest,
    db: DbSession,
    current_user: CurrentUser,
) -> MeetingRegistryRescheduleApproveRead:
    """Подтвердить перенос: обновить встречу в Outlook и запись реестра."""
    await _require_agent_access(db, current_user)
    try:
        result = await MeetingService(db).approve_registry_reschedule(
            str(memo_ref_key),
            payload,
            current_user=current_user,
        )
        await db.commit()
        return result
    except MeetingServiceError as exc:
        await db.rollback()
        raise _service_error(exc) from exc


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
        raise _service_error(exc) from exc


@router.get("/memos/{memo_ref_key}/detail", response_model=MeetingMemoDetailRead)
async def get_meeting_memo_detail(
    memo_ref_key: uuid.UUID,
    db: DbSession,
    current_user: CurrentUser,
    force_refresh: bool = False,
) -> MeetingMemoDetailRead:
    await _require_agent_access(db, current_user)
    try:
        payload, _fetched_at, _from_cache = await MeetingMemoCacheService().get_memo_detail(
            str(memo_ref_key),
            force_refresh=force_refresh,
        )
        return MeetingMemoDetailRead.model_validate(payload)
    except MemoCacheMissError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/memos/{memo_ref_key}/agent/slot-preview", response_model=MeetingAgentSlotPreviewRead)
async def preview_meeting_agent_slot(
    memo_ref_key: uuid.UUID,
    db: DbSession,
    current_user: CurrentUser,
    payload: MeetingAgentSlotPreviewRequest | None = None,
) -> MeetingAgentSlotPreviewRead:
    """Ближайший свободный слот для модалки «Запустить агента» (участники + инициатор + руководитель)."""
    await _require_agent_access(db, current_user)
    return await MeetingService(db).suggest_agent_slot_safe(
        str(memo_ref_key),
        payload or MeetingAgentSlotPreviewRequest(),
        current_user=current_user,
    )


@router.post(
    "/memos/{memo_ref_key}/agent/slot-preview/details",
    response_model=MeetingAgentSlotDetailRead,
)
async def get_meeting_agent_slot_details(
    memo_ref_key: uuid.UUID,
    payload: MeetingAgentSlotDetailRequest,
    db: DbSession,
    current_user: CurrentUser,
) -> MeetingAgentSlotDetailRead:
    """Детали выбранного слота: кто свободен/занят и какие встречи мешают."""
    await _require_agent_access(db, current_user)
    return await MeetingService(db).get_agent_slot_detail_safe(
        str(memo_ref_key),
        payload,
        current_user=current_user,
    )


@router.post(
    "/memos/{memo_ref_key}/manual/slot-details",
    response_model=MeetingAgentSlotDetailRead,
)
async def validate_manual_meeting_slot(
    memo_ref_key: uuid.UUID,
    payload: MeetingAgentSlotDetailRequest,
    db: DbSession,
    current_user: CurrentUser,
) -> MeetingAgentSlotDetailRead:
    """П.2: «Запланировать вручную» — проверка выбранного пользователем слота."""
    await _require_agent_access(db, current_user)
    return await MeetingService(db).get_agent_slot_detail_safe(
        str(memo_ref_key),
        payload,
        current_user=current_user,
    )


@router.post(
    "/registry/{memo_ref_key}/reschedule/slot-details",
    response_model=MeetingAgentSlotDetailRead,
)
async def get_registry_reschedule_slot_details(
    memo_ref_key: uuid.UUID,
    payload: MeetingAgentSlotDetailRequest,
    db: DbSession,
    current_user: CurrentUser,
) -> MeetingAgentSlotDetailRead:
    """П.3 (ручной): проверка выбранного слота при переносе в реестре."""
    await _require_agent_access(db, current_user)
    try:
        return await MeetingService(db).get_registry_reschedule_slot_detail(
            str(memo_ref_key),
            payload,
            current_user=current_user,
        )
    except MeetingServiceError as exc:
        raise _service_error(exc) from exc


@router.post("/memos/{memo_ref_key}/agent/approve", response_model=MeetingAgentSlotApproveRead)
async def approve_meeting_agent_slot(
    memo_ref_key: uuid.UUID,
    payload: MeetingAgentSlotApproveRequest,
    db: DbSession,
    current_user: CurrentUser,
) -> MeetingAgentSlotApproveRead:
    """Утвердить слот и разослать приглашения через Outlook/EWS (без запросов в 1С)."""
    await _require_agent_access(db, current_user)
    try:
        result = await MeetingService(db).approve_agent_slot(
            str(memo_ref_key),
            payload,
            current_user=current_user,
        )
        await db.commit()
        return result
    except MeetingServiceError as exc:
        await db.rollback()
        raise _service_error(exc) from exc


@router.post("/memos/{memo_ref_key}/approve", response_model=MeetingMemoApproveRead)
async def approve_meeting_memo(
    memo_ref_key: uuid.UUID,
    payload: MeetingMemoApproveRequest,
    db: DbSession,
    current_user: CurrentUser,
) -> MeetingMemoApproveRead:
    """Согласовать СЗ в 1С: статус «Согласована», поля исполнителя УД."""
    await _require_agent_access(db, current_user)
    try:
        result = await MeetingService(db).approve_memo(
            str(memo_ref_key),
            payload,
            current_user=current_user,
        )
        await db.commit()
        return result
    except MeetingServiceError as exc:
        await db.rollback()
        raise _service_error(exc) from exc


@router.post("/memos/{memo_ref_key}/reject", response_model=MeetingMemoRejectRead)
async def reject_meeting_memo(
    memo_ref_key: uuid.UUID,
    payload: MeetingMemoRejectRequest,
    db: DbSession,
    current_user: CurrentUser,
) -> MeetingMemoRejectRead:
    """Отклонить СЗ в 1С: статус «Отклонена», причина в комментарии, уведомление инициатору."""
    await _require_agent_access(db, current_user)
    try:
        result = await MeetingService(db).reject_memo(
            str(memo_ref_key),
            payload,
            current_user=current_user,
        )
        await db.commit()
        return result
    except MeetingServiceError as exc:
        await db.rollback()
        raise _service_error(exc) from exc


@router.post("/slots", response_model=list[MeetingSlotRead])
async def find_meeting_slots(
    payload: MeetingSlotsRequest,
    db: DbSession,
    current_user: CurrentUser,
) -> list[MeetingSlotRead]:
    try:
        return await MeetingService(db).find_slots(payload, current_user=current_user)
    except MeetingServiceError as exc:
        raise _service_error(exc) from exc


@router.post("/rooms", response_model=list[MeetingRoomRead])
async def find_meeting_rooms(
    payload: MeetingRoomsRequest,
    db: DbSession,
    current_user: CurrentUser,
) -> list[MeetingRoomRead]:
    try:
        return await MeetingService(db).find_rooms(payload, current_user=current_user)
    except MeetingServiceError as exc:
        raise _service_error(exc) from exc


@router.post("/invite/preview", response_model=MeetingInviteDraftRead)
async def preview_meeting_invite(
    payload: MeetingInvitePreviewRequest,
    db: DbSession,
    current_user: CurrentUser,
) -> MeetingInviteDraftRead:
    try:
        return await MeetingService(db).preview_invite(payload, current_user=current_user)
    except MeetingServiceError as exc:
        raise _service_error(exc) from exc


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
        raise _service_error(exc) from exc


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
        raise _service_error(exc) from exc


@router.get("/runs/{task_id}", response_model=MeetingRunResultRead)
async def get_meeting_run(
    task_id: uuid.UUID,
    db: DbSession,
    current_user: CurrentUser,
) -> MeetingRunResultRead:
    try:
        return await MeetingService(db).get_run(task_id, current_user=current_user)
    except MeetingServiceError as exc:
        raise _service_error(exc, status_code=status.HTTP_404_NOT_FOUND) from exc
