from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, status

from app.agents.meeting_agent.dashboard import load_login_context
from app.services.meeting_memo_cache import MeetingMemoCacheService, MemoCacheMissError
from app.services.meeting_memo_series_service import (
    MeetingMemoSeriesService,
    MeetingMemoSeriesServiceError,
)
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
    MeetingMemoSeriesPlanningChoiceRead,
    MeetingMemoSeriesPlanningChoiceRequest,
    MeetingMemoSeriesCreateRead,
    MeetingMemoSeriesCreateRequest,
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
    MeetingRegistryMeetingTopicSaveRead,
    MeetingRegistryParticipantsRead,
    MeetingRegistryParticipantSearchRead,
    MeetingRegistryParticipantsApplyRead,
    MeetingRegistryParticipantsApplyRequest,
    MeetingRegistryParticipantsAddConfirmRead,
    MeetingRegistryParticipantsAddConfirmRequest,
    MeetingRegistryParticipantsRemovalConfirmRead,
    MeetingRegistryParticipantsRemovalConfirmRequest,
    MeetingRegistryProtocolDraftDispatchRead,
    MeetingRegistryProtocolCreateRead,
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
from app.schemas.scheduled_meeting import (
    MeetingCategoryRead,
    ScheduledMeetingCancelRead,
    ScheduledMeetingCancelRequest,
    ScheduledMeetingCreate,
    ScheduledMeetingDetailRead,
    ScheduledMeetingEmployeeOptionRead,
    ScheduledMeetingParticipantOptionRead,
    ScheduledMeetingPlanPreviewRead,
    ScheduledMeetingPlanPreviewRequest,
    ScheduledMeetingPlanRequest,
    ScheduledMeetingPositionResolveRead,
    ScheduledMeetingPositionResolveRequest,
    ScheduledMeetingRead,
    ScheduledMeetingUpdate,
    ScheduledMeetingUpdateRead,
)
from app.schemas.meeting_topic import (
    MeetingTopicCheckSimilarRead,
    MeetingTopicCheckSimilarRequest,
    MeetingTopicResolveRead,
    MeetingTopicResolveRequest,
    MeetingTopicValidationRead,
)
from app.services.scheduled_meeting_service import (
    ScheduledMeetingService,
    ScheduledMeetingServiceError,
)
from app.services.meeting_topic_service import (
    MeetingTopicService,
    MeetingTopicServiceError,
)

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


def _scheduled_meeting_error(exc: ScheduledMeetingServiceError) -> HTTPException:
    return HTTPException(exc.status_code, detail=str(exc))


def _meeting_topic_error(exc: MeetingTopicServiceError) -> HTTPException:
    return HTTPException(exc.status_code, detail=str(exc))


@router.get(
    "/scheduled/category-options",
    response_model=list[MeetingCategoryRead],
)
async def list_scheduled_meeting_category_options(
    db: DbSession,
    current_user: CurrentUser,
) -> list[MeetingCategoryRead]:
    """Виды совещаний для графика (внутренний классификатор)."""
    await _require_agent_access(db, current_user)
    return await ScheduledMeetingService(db).list_category_options()


@router.get(
    "/scheduled/employee-options",
    response_model=list[ScheduledMeetingEmployeeOptionRead],
)
async def list_scheduled_meeting_employee_options(
    db: DbSession,
    current_user: CurrentUser,
    search: str | None = None,
    limit: int = 20,
) -> list[ScheduledMeetingEmployeeOptionRead]:
    """Сотрудники для выбора руководителя, ответственного и участников графика."""
    await _require_agent_access(db, current_user)
    return await ScheduledMeetingService(db).list_employee_options(
        search=search,
        limit=limit,
    )


@router.post(
    "/scheduled/resolve-positions",
    response_model=ScheduledMeetingPositionResolveRead,
)
async def resolve_scheduled_meeting_positions(
    payload: ScheduledMeetingPositionResolveRequest,
    db: DbSession,
    current_user: CurrentUser,
) -> ScheduledMeetingPositionResolveRead:
    """Подставить сотрудников по должностям (для заполнения серии из нормативки)."""
    await _require_agent_access(db, current_user)
    return await ScheduledMeetingService(db).resolve_positions(payload.position_ids)


@router.get(
    "/scheduled/participant-options",
    response_model=list[ScheduledMeetingParticipantOptionRead],
)
async def list_scheduled_meeting_participant_options(
    db: DbSession,
    current_user: CurrentUser,
    search: str | None = None,
    limit: int = 100,
) -> list[ScheduledMeetingParticipantOptionRead]:
    """Должности для выбора участников графика совещаний."""
    await _require_agent_access(db, current_user)
    return await ScheduledMeetingService(db).list_participant_options(
        search=search,
        limit=limit,
    )


@router.get("/scheduled", response_model=list[ScheduledMeetingRead])
async def list_scheduled_meetings(
    db: DbSession,
    current_user: CurrentUser,
) -> list[ScheduledMeetingRead]:
    """Список серий совещаний из графика.

    При открытии вкладки дополнительно опрашивает TurboProject (с cooldown)
    и создаёт in-app уведомления о новых проектах (без Outlook и без автосоздания серии).
    """
    await _require_agent_access(db, current_user)
    from app.services.turbo_project_series_sync_trigger import (
        maybe_sync_turbo_projects_on_schedule_open,
    )

    await maybe_sync_turbo_projects_on_schedule_open(db)
    service = ScheduledMeetingService(db)
    await service.archive_expired_series()
    await db.commit()
    return await service.list()


@router.post(
    "/scheduled",
    response_model=ScheduledMeetingRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_scheduled_meeting(
    db: DbSession,
    current_user: CurrentUser,
    payload: ScheduledMeetingCreate,
) -> ScheduledMeetingRead:
    """Добавление серии совещаний в график."""
    await _require_agent_access(db, current_user)
    try:
        meeting = await ScheduledMeetingService(db).create(payload)
        await db.commit()
        return meeting
    except ScheduledMeetingServiceError as exc:
        await db.rollback()
        raise _scheduled_meeting_error(exc) from exc


@router.post(
    "/scheduled/{meeting_id}/plan-preview",
    response_model=ScheduledMeetingPlanPreviewRead,
)
async def plan_preview_scheduled_meeting(
    db: DbSession,
    current_user: CurrentUser,
    meeting_id: uuid.UUID,
    payload: ScheduledMeetingPlanPreviewRequest | None = None,
) -> ScheduledMeetingPlanPreviewRead:
    """Проверка конфликтов серии и предложения soft_week до распланирования в Outlook."""
    await _require_agent_access(db, current_user)
    try:
        return await ScheduledMeetingService(db).plan_preview(meeting_id, payload)
    except ScheduledMeetingServiceError as exc:
        raise _scheduled_meeting_error(exc) from exc


@router.post("/scheduled/{meeting_id}/plan", response_model=ScheduledMeetingRead)
async def plan_scheduled_meeting(
    db: DbSession,
    current_user: CurrentUser,
    meeting_id: uuid.UUID,
    payload: ScheduledMeetingPlanRequest | None = None,
) -> ScheduledMeetingRead:
    """Создание серии совещаний в Outlook по правилу из графика."""
    await _require_agent_access(db, current_user)
    try:
        meeting = await ScheduledMeetingService(db).plan(meeting_id, payload)
        await db.commit()
        return meeting
    except ScheduledMeetingServiceError as exc:
        await db.rollback()
        raise _scheduled_meeting_error(exc) from exc


@router.post("/scheduled/{meeting_id}/cancel", response_model=ScheduledMeetingCancelRead)
async def cancel_scheduled_meeting(
    db: DbSession,
    current_user: CurrentUser,
    meeting_id: uuid.UUID,
    payload: ScheduledMeetingCancelRequest | None = None,
) -> ScheduledMeetingCancelRead:
    """Отмена распланированной серии в Outlook и перевод записи в архив."""
    await _require_agent_access(db, current_user)
    body = payload or ScheduledMeetingCancelRequest()
    try:
        result = await ScheduledMeetingService(db).cancel(
            meeting_id,
            body,
            current_user=current_user,
        )
        await db.commit()
        return result
    except ScheduledMeetingServiceError as exc:
        await db.rollback()
        raise _scheduled_meeting_error(exc) from exc


@router.post("/topics/check-similar", response_model=MeetingTopicCheckSimilarRead)
async def check_similar_meeting_topic(
    db: DbSession,
    current_user: CurrentUser,
    payload: MeetingTopicCheckSimilarRequest,
) -> MeetingTopicCheckSimilarRead:
    """Проверка похожей темы у руководителя перед созданием новой."""
    await _require_agent_access(db, current_user)
    try:
        return await MeetingTopicService().check_similar(payload)
    except MeetingTopicServiceError as exc:
        raise _meeting_topic_error(exc) from exc
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/topics/resolve", response_model=MeetingTopicResolveRead)
async def resolve_meeting_topic(
    db: DbSession,
    current_user: CurrentUser,
    payload: MeetingTopicResolveRequest,
) -> MeetingTopicResolveRead:
    """Подтверждение существующей темы или создание новой после проверки."""
    await _require_agent_access(db, current_user)
    try:
        return await MeetingTopicService().resolve(payload)
    except MeetingTopicServiceError as exc:
        raise _meeting_topic_error(exc) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc


@router.get("/topics/{topic_ref_key}/validate", response_model=MeetingTopicValidationRead)
async def validate_meeting_topic_ref(
    topic_ref_key: uuid.UUID,
    db: DbSession,
    current_user: CurrentUser,
) -> MeetingTopicValidationRead:
    """Проверяет, что тема существует в 1С и пригодна для совещания."""
    await _require_agent_access(db, current_user)
    return await MeetingTopicService().validate_topic_ref_key(str(topic_ref_key))


@router.get("/scheduled/{meeting_id}", response_model=ScheduledMeetingRead)
async def get_scheduled_meeting(
    db: DbSession,
    current_user: CurrentUser,
    meeting_id: uuid.UUID,
) -> ScheduledMeetingRead:
    """Карточка серии из БД без синхронизации Outlook."""
    await _require_agent_access(db, current_user)
    try:
        return await ScheduledMeetingService(db).get(meeting_id)
    except ScheduledMeetingServiceError as exc:
        raise _scheduled_meeting_error(exc) from exc


@router.patch("/scheduled/{meeting_id}", response_model=ScheduledMeetingUpdateRead)
async def update_scheduled_meeting(
    db: DbSession,
    current_user: CurrentUser,
    meeting_id: uuid.UUID,
    payload: ScheduledMeetingUpdate,
) -> ScheduledMeetingUpdateRead:
    """Изменение серии: срок (сокращение/продление) и комментарий."""
    await _require_agent_access(db, current_user)
    try:
        result = await ScheduledMeetingService(db).update(meeting_id, payload)
        await db.commit()
        return result
    except ScheduledMeetingServiceError as exc:
        await db.rollback()
        raise _scheduled_meeting_error(exc) from exc


@router.get("/scheduled/{meeting_id}/detail", response_model=ScheduledMeetingDetailRead)
async def get_scheduled_meeting_detail(
    db: DbSession,
    current_user: CurrentUser,
    meeting_id: uuid.UUID,
) -> ScheduledMeetingDetailRead:
    """Детали серии: текущая карточка реестра и история событий."""
    await _require_agent_access(db, current_user)
    try:
        detail = await ScheduledMeetingService(db).get_detail(meeting_id)
        await db.commit()
        return detail
    except ScheduledMeetingServiceError as exc:
        await db.rollback()
        raise _scheduled_meeting_error(exc) from exc


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


@router.post(
    "/registry/dispatch-protocol-drafts",
    response_model=MeetingRegistryProtocolDraftDispatchRead,
)
async def dispatch_registry_protocol_drafts(
    db: DbSession,
    current_user: CurrentUser,
) -> MeetingRegistryProtocolDraftDispatchRead:
    """Поставить в Celery создание черновиков протоколов для подходящих карточек реестра."""
    try:
        result = await MeetingService(db).dispatch_protocol_drafts(current_user=current_user)
        await db.commit()
        return MeetingRegistryProtocolDraftDispatchRead.model_validate(result)
    except MeetingServiceError as exc:
        await db.rollback()
        raise _service_error(exc) from exc


@router.post(
    "/registry/{memo_ref_key}/create-protocol",
    response_model=MeetingRegistryProtocolCreateRead,
)
async def create_registry_protocol(
    memo_ref_key: uuid.UUID,
    db: DbSession,
    current_user: CurrentUser,
) -> MeetingRegistryProtocolCreateRead:
    """Создать черновик протокола в 1С для карточки реестра."""
    await _require_agent_access(db, current_user)
    try:
        result = await MeetingService(db).create_registry_protocol(
            str(memo_ref_key),
            current_user=current_user,
        )
        await db.commit()
        return MeetingRegistryProtocolCreateRead.model_validate(result)
    except MeetingServiceError as exc:
        await db.rollback()
        raise _service_error(exc) from exc


@router.post(
    "/registry/{memo_ref_key}/meeting-topic",
    response_model=MeetingRegistryMeetingTopicSaveRead,
)
async def save_registry_meeting_topic(
    memo_ref_key: uuid.UUID,
    payload: MeetingTopicResolveRead,
    db: DbSession,
    current_user: CurrentUser,
) -> MeetingRegistryMeetingTopicSaveRead:
    """Сохранить выбранную тему совещания в карточке реестра для автосоздания протокола."""
    await _require_agent_access(db, current_user)
    try:
        item = await MeetingService(db).save_registry_meeting_topic(
            str(memo_ref_key),
            topic_resolution=payload,
            current_user=current_user,
        )
        await db.commit()
        return MeetingRegistryMeetingTopicSaveRead(
            ref_key=item.ref_key,
            topic_ref_key=payload.topic.ref_key,
            topic_code=payload.topic.code,
            topic_description=payload.topic.description,
            meeting_type=payload.topic.meeting_type,
            protocol_draft_at=item.protocol_draft_at,
        )
    except MeetingServiceError as exc:
        await db.rollback()
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


@router.post(
    "/memos/{memo_ref_key}/series-planning-choice",
    response_model=MeetingMemoSeriesPlanningChoiceRead,
)
async def save_meeting_memo_series_planning_choice(
    memo_ref_key: uuid.UUID,
    payload: MeetingMemoSeriesPlanningChoiceRequest,
    db: DbSession,
    current_user: CurrentUser,
) -> MeetingMemoSeriesPlanningChoiceRead:
    await _require_agent_access(db, current_user)
    cache = MeetingMemoCacheService()
    normalized = str(memo_ref_key).strip().lower()
    try:
        await cache.set_series_planning_choice(normalized, payload.mode)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return MeetingMemoSeriesPlanningChoiceRead(ref_key=normalized, mode=payload.mode)


@router.post(
    "/memos/{memo_ref_key}/create-series",
    response_model=MeetingMemoSeriesCreateRead,
)
async def create_meeting_memo_series(
    memo_ref_key: uuid.UUID,
    db: DbSession,
    current_user: CurrentUser,
    payload: MeetingMemoSeriesCreateRequest | None = None,
) -> MeetingMemoSeriesCreateRead:
    """Создать серию в графике (статус «создано») и согласовать СЗ. Без Outlook."""
    await _require_agent_access(db, current_user)
    normalized = str(memo_ref_key).strip().lower()
    body = payload or MeetingMemoSeriesCreateRequest()
    try:
        result = await MeetingMemoSeriesService(db).create_series_from_memo(
            normalized,
            current_user=current_user,
            meeting_topic=body.meeting_topic,
        )
        await db.commit()
    except MeetingMemoSeriesServiceError as exc:
        await db.rollback()
        raise HTTPException(exc.status_code, detail=str(exc)) from exc
    except Exception as exc:
        await db.rollback()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    created = result.scheduled_meeting
    return MeetingMemoSeriesCreateRead(
        ref_key=normalized,
        scheduled_meeting_id=created.id,
        scheduled_meeting_title=created.title,
        recurrence_label=created.recurrence_label,
        occurrence_count=result.occurrence_count,
        memo_approved=result.memo_approved,
        memo_approve_message=result.memo_approve_message,
    )


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
