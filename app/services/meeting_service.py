from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.meeting_backend import (
    InviteDraft,
    MeetingBackend,
    MeetingBackendError,
    MeetingMemo,
    MeetingQuorumSlot,
    MeetingRoomOption,
    MeetingSlot,
    MeetingSlotConflict,
    ResolvedParticipant,
    _duration_from_memo,
    _normalize_memo,
)
from app.services.meeting_agent_slot import MeetingAgentSlotService
from app.services.meeting_constants import (
    QUORUM_MAX_CANDIDATES,
    QUORUM_MIN_COVERAGE_RATIO,
    QUORUM_VERIFY_TOP_N,
    SLOT_PREVIEW_MAX_DAYS,
    SLOT_PREVIEW_TIMEOUT_SECONDS,
)
from app.services.meeting_agent_slot_responses import (
    agent_slot_detail_error,
    agent_slot_preview_error,
)
from app.services.meeting_mappers import (
    attendee_weights_from_attendees,
    conflict_read,
    coverage_read,
    email_roles_from_attendees,
    invite_read,
    leadership_required_emails,
    memo_read,
    participant_status_read,
    quorum_slot_is_fully_free,
    quorum_slot_read,
    registry_item_read,
    room_read,
    slot_read,
)
from app.agents.meeting_agent.config import AGENT_NAME
from app.models.agent import Agent
from app.models.enums import TaskStatus
from app.models.task import Task
from app.models.user import User
from app.schemas.meeting import (
    MeetingAgentSlotApproveRead,
    MeetingAgentSlotApproveRequest,
    MeetingAgentSlotDetailRead,
    MeetingAgentSlotDetailRequest,
    MeetingAgentSlotPreviewRead,
    MeetingAgentSlotPreviewRequest,
    MeetingAttendeeRead,
    MeetingInviteDraftRead,
    MeetingInvitePreviewRequest,
    MeetingInviteSendRequest,
    MeetingMemoRead,
    MeetingMemoApproveRead,
    MeetingMemoApproveRequest,
    MeetingMemoRejectRead,
    MeetingMemoRejectRequest,
    MeetingRegistryRead,
    MeetingRegistryItemRead,
    MeetingRoomRead,
    MeetingRoomsRequest,
    MeetingRunCreate,
    MeetingRunRead,
    MeetingRunResultRead,
    MeetingQuorumSlotRead,
    MeetingSlotCoverageRead,
    MeetingSlotParticipantStatusRead,
    MeetingSlotRead,
    MeetingSlotsRequest,
)
from app.schemas.task import TaskResultCreate
from app.services.audit_service import AuditService
from app.services.meeting_agent_approve import (
    MeetingApproveError,
    build_approve_invite_body,
    resolve_approve_recipients,
)
from app.services.meeting_attendee_priority import (
    priority_role_label,
    weight_for_priority_role,
)
from app.services.meeting_agent_errors import (
    format_calendar_error,
    format_email_lookup_error,
    format_missing_emails_error,
    format_partial_slot_preview_note,
    format_no_slot_error,
    format_reschedule_suggestions_note,
    format_onec_load_error,
    format_participants_missing_error,
    format_slot_preview_timeout_error,
)
from app.services.meeting_attendees import (
    collect_attendees_from_detail,
    emails_by_fio_from_detail,
    person_from_detail_by_fio,
)
from app.services.meeting_offline_cache import (
    build_offline_approve_result,
    is_offline_cache_detail,
)
from app.services.meeting_dashboard_cache import MeetingDashboardCacheService
from app.services.meeting_permission import MEETING_AGENT_SLUG, can_access_meeting_agent
from app.services.meeting_registry_service import MeetingRegistryService, build_stage_counts
from app.core.logging import get_logger
from app.services.meeting_duration import resolve_duration_minutes
from app.services.meeting_invite_format import (
    format_invite_location_from_detail,
    resolve_invite_subject,
    resolve_room_for_location,
)
from app.services.meeting_slot import (
    format_planned_start_for_search,
    format_search_start_from_meeting_date,
    format_slot_label,
    parse_slot_datetime,
    slot_duration_minutes,
)
from app.services.permission_service import PermissionService
from app.services.task_service import TaskService
from app.tools.Outlook.find_meeting_slot import build_slot_participant_details
from app.tools.Outlook.send_meeting_invite import dispatch_meeting_invite, load_config
from app.tools.onec.approve_service_memo import approve_service_memo
from app.tools.onec.reject_service_memo import reject_service_memo
from app.services.meeting_memo_cache import (
    MeetingMemoCacheService,
    MemoCacheMissError,
    detail_to_memo_document,
)
from app.tools.onec.service_memo_shared import APPROVED_STATUS, ServiceMemoWorkflowError

logger = get_logger(__name__)


from app.services.meeting_exceptions import MeetingServiceError


class MeetingService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.audit = AuditService(db)

    async def load_memo(
        self,
        *,
        current_user: User,
        memo_ref_key: str | None = None,
        memo_number: str | None = None,
    ) -> MeetingMemoRead:
        await self._ensure_access(current_user)
        backend = self._backend()
        try:
            memo = await backend.load_memo(
                memo_ref_key=memo_ref_key,
                memo_number=memo_number,
                current_user=current_user,
            )
        except MeetingBackendError as exc:
            raise MeetingServiceError(str(exc)) from exc
        return memo_read(memo)

    async def find_slots(
        self,
        payload: MeetingSlotsRequest,
        *,
        current_user: User,
    ) -> list[MeetingSlotRead]:
        await self._ensure_access(current_user)
        backend = self._backend()
        memo = await self._load_memo_optional(
            backend,
            current_user=current_user,
            memo_ref_key=str(payload.memo_ref_key) if payload.memo_ref_key else None,
            memo_number=payload.memo_number,
        )
        participants = await backend.resolve_participants(
            payload.participant_fio or (memo.participant_fio if memo else []),
            current_user=current_user,
        )
        try:
            slots = await backend.find_slots(
                memo=memo,
                participants=participants,
                planned_start=payload.planned_start.isoformat() if payload.planned_start else None,
                duration_minutes=payload.duration_minutes,
                current_user=current_user,
            )
        except MeetingBackendError as exc:
            raise MeetingServiceError(str(exc)) from exc
        return [slot_read(item) for item in slots]
    def _slot_service(self) -> MeetingAgentSlotService:
        return MeetingAgentSlotService(self.db, backend_factory=self._backend)

    async def suggest_agent_slot(
        self,
        memo_ref_key: str,
        payload: MeetingAgentSlotPreviewRequest,
        *,
        current_user: User,
    ) -> MeetingAgentSlotPreviewRead:
        await self._ensure_access(current_user)
        return await self._slot_service().suggest_agent_slot(
            memo_ref_key, payload, current_user=current_user
        )

    async def suggest_agent_slot_safe(
        self,
        memo_ref_key: str,
        payload: MeetingAgentSlotPreviewRequest,
        *,
        current_user: User,
    ) -> MeetingAgentSlotPreviewRead:
        return await self._slot_service().suggest_agent_slot_safe(
            memo_ref_key, payload, current_user=current_user
        )

    async def get_agent_slot_detail(
        self,
        memo_ref_key: str,
        payload: MeetingAgentSlotDetailRequest,
        *,
        current_user: User,
    ) -> MeetingAgentSlotDetailRead:
        await self._ensure_access(current_user)
        return await self._slot_service().get_agent_slot_detail(
            memo_ref_key, payload, current_user=current_user
        )

    async def get_agent_slot_detail_safe(
        self,
        memo_ref_key: str,
        payload: MeetingAgentSlotDetailRequest,
        *,
        current_user: User,
    ) -> MeetingAgentSlotDetailRead:
        return await self._slot_service().get_agent_slot_detail_safe(
            memo_ref_key, payload, current_user=current_user
        )


    async def approve_agent_slot(
        self,
        memo_ref_key: str,
        payload: MeetingAgentSlotApproveRequest,
        *,
        current_user: User,
    ) -> MeetingAgentSlotApproveRead:
        """Отправляет приглашения через Outlook/EWS. Без 1С: только e-mail и слот из запроса."""
        await self._ensure_access(current_user)
        normalized_ref = memo_ref_key.strip().lower()
        memo_detail: dict | None = None
        try:
            memo_detail, _, _ = await MeetingMemoCacheService().get_memo_detail(normalized_ref)
        except MemoCacheMissError:
            pass

        try:
            attendee_details, resolved = resolve_approve_recipients(payload)
        except MeetingApproveError as exc:
            raise MeetingServiceError(str(exc)) from exc

        emails = [item.email for item in resolved if item.email]
        if not emails:
            raise MeetingServiceError("Не указаны e-mail участников для отправки приглашения")

        subject = resolve_invite_subject(memo_detail, override=payload.subject)
        location = format_invite_location_from_detail(memo_detail, override=payload.location)
        duration = slot_duration_minutes(payload.slot_start, payload.slot_end)
        room = resolve_room_for_location(location)
        body = build_approve_invite_body(attendee_details, room=room)
        resources = [room["email"]] if room and room.get("email") else []

        try:
            sent_payload = await asyncio.to_thread(
                dispatch_meeting_invite,
                attendee=emails[0],
                attendees=emails,
                subject=subject,
                start=payload.slot_start,
                duration_minutes=duration,
                body=body,
                location=location,
                resources=resources,
            )
        except Exception as exc:
            raise MeetingServiceError(
                f"Не удалось отправить приглашение через Outlook/Exchange: {exc}"
            ) from exc

        await self.audit.log(
            action="meeting.agent_slot_approved",
            actor_id=current_user.id,
            resource_type="meeting_memo",
            resource_id=normalized_ref,
            payload={
                "subject": subject,
                "start": payload.slot_start,
                "end": payload.slot_end,
                "attendees": emails,
            },
        )

        await MeetingRegistryService(self.db).upsert_from_invite(
            memo_ref_key=normalized_ref,
            slot_start=payload.slot_start,
            slot_end=payload.slot_end,
            subject=subject,
            location=location or None,
            attendees=emails,
            approved_by=current_user,
            memo_detail=memo_detail,
            sent_payload=sent_payload if isinstance(sent_payload, dict) else None,
        )

        await self._sync_offline_cache_after_invite(
            normalized_ref,
            memo_detail=memo_detail,
            approver_fio=_user_fio(current_user),
        )

        return MeetingAgentSlotApproveRead(
            memo_ref_key=normalized_ref,
            subject=subject,
            start=payload.slot_start,
            end=payload.slot_end,
            slot_label=format_slot_label(payload.slot_start, payload.slot_end),
            location=location or None,
            attendees=sent_payload.get("attendees") or emails,
            attendee_details=attendee_details,
            sent=True,
            outlook_item_id=sent_payload.get("outlook_item_id"),
            outlook_changekey=sent_payload.get("outlook_changekey"),
            outlook_meeting_url=sent_payload.get("outlook_meeting_url"),
        )

    async def reject_memo(
        self,
        memo_ref_key: str,
        payload: MeetingMemoRejectRequest,
        *,
        current_user: User,
    ) -> MeetingMemoRejectRead:
        await self._ensure_access(current_user)
        normalized_ref = memo_ref_key.strip().lower()
        rejector_fio = _user_fio(current_user)

        try:
            raw = await asyncio.to_thread(
                reject_service_memo,
                ref_key=normalized_ref,
                reason=payload.reason,
                rejector_fio=rejector_fio,
                notify_initiator=payload.notify_initiator,
            )
        except ServiceMemoWorkflowError as exc:
            raise MeetingServiceError(str(exc)) from exc
        except Exception as exc:
            lowered = str(exc).lower()
            if any(token in lowered for token in ("401", "403", "404", "timeout", "connection", "connect", "odata")):
                raise MeetingServiceError(format_onec_load_error(exc), status_code=503) from exc
            raise MeetingServiceError(f"Не удалось отклонить служебную записку в 1С: {exc}") from exc

        await self.audit.log(
            action="meeting.memo_rejected",
            actor_id=current_user.id,
            resource_type="meeting_memo",
            resource_id=normalized_ref,
            payload={
                "number": raw.get("number"),
                "reason": raw.get("reason"),
                "changed": raw.get("changed"),
                "notification_sent": raw.get("notification_sent"),
            },
        )

        if raw.get("status"):
            await self._apply_memo_status_to_cache(normalized_ref, str(raw["status"]))
        if raw.get("changed"):
            self._schedule_memo_cache_refresh(normalized_ref)

        return MeetingMemoRejectRead.model_validate(raw)

    async def approve_memo(
        self,
        memo_ref_key: str,
        payload: MeetingMemoApproveRequest,
        *,
        current_user: User,
    ) -> MeetingMemoApproveRead:
        await self._ensure_access(current_user)
        normalized_ref = memo_ref_key.strip().lower()
        approver_fio = _user_fio(current_user)

        cache_service = MeetingMemoCacheService()
        cached = await cache_service.read_cached(normalized_ref)
        is_offline_cache = cached is not None and is_offline_cache_detail(cached["payload"])

        if is_offline_cache:
            raw = build_offline_approve_result(
                cached["payload"],
                ref_key=normalized_ref,
                approver_fio=approver_fio,
                comment=payload.comment,
            )
            if raw.get("changed"):
                history_message = (
                    f"Согласована офлайн ({approver_fio})"
                    if approver_fio
                    else "Согласована офлайн (offline cache)"
                )
                await cache_service.patch_status(
                    normalized_ref,
                    APPROVED_STATUS,
                    history_message=history_message,
                )
                await self._apply_memo_status_to_cache(normalized_ref, APPROVED_STATUS)
        else:
            try:
                raw = await asyncio.to_thread(
                    approve_service_memo,
                    ref_key=normalized_ref,
                    approver_fio=approver_fio,
                    comment=payload.comment,
                    perform_approval=True,
                )
            except ServiceMemoWorkflowError as exc:
                raise MeetingServiceError(str(exc)) from exc
            except Exception as exc:
                lowered = str(exc).lower()
                if any(token in lowered for token in ("401", "403", "404", "timeout", "connection", "connect", "odata")):
                    raise MeetingServiceError(format_onec_load_error(exc), status_code=503) from exc
                raise MeetingServiceError(f"Не удалось согласовать служебную записку в 1С: {exc}") from exc

            if raw.get("status"):
                await self._apply_memo_status_to_cache(normalized_ref, str(raw["status"]))
            if raw.get("changed"):
                self._schedule_memo_cache_refresh(normalized_ref)

        await self.audit.log(
            action="meeting.memo_approved",
            actor_id=current_user.id,
            resource_type="meeting_memo",
            resource_id=normalized_ref,
            payload={
                "number": raw.get("number"),
                "changed": raw.get("changed"),
                "sto_ready": raw.get("sto_ready"),
                "offline_cache": is_offline_cache,
            },
        )

        return MeetingMemoApproveRead.model_validate(raw)

    async def _sync_offline_cache_after_invite(
        self,
        memo_ref_key: str,
        *,
        memo_detail: dict | None,
        approver_fio: str | None,
    ) -> None:
        detail = memo_detail
        if detail is None:
            cached = await MeetingMemoCacheService().read_cached(memo_ref_key)
            detail = cached["payload"] if cached else None
        if not is_offline_cache_detail(detail):
            return
        if str((detail or {}).get("status") or "") == APPROVED_STATUS:
            return
        history_message = (
            f"Согласована офлайн при отправке приглашений ({approver_fio})"
            if approver_fio
            else "Согласована офлайн при отправке приглашений"
        )
        await MeetingMemoCacheService().patch_status(
            memo_ref_key,
            APPROVED_STATUS,
            history_message=history_message,
        )
        await self._apply_memo_status_to_cache(memo_ref_key, APPROVED_STATUS)

    async def _apply_memo_status_to_cache(self, memo_ref_key: str, status: str) -> None:
        from app.core.config import settings

        if not settings.MEETING_DASHBOARD_CACHE_ENABLED:
            return
        normalized = memo_ref_key.strip().lower()
        try:
            memo_updated = await MeetingMemoCacheService().patch_status(normalized, status)
            dashboard_updated = await MeetingDashboardCacheService().patch_status(normalized, status)
            logger.info(
                "meeting_memo_cache_status_patched",
                ref_key=normalized,
                status=status,
                memo_cache=memo_updated,
                dashboard_cache=dashboard_updated,
            )
        except Exception as exc:
            logger.warning(
                "meeting_memo_cache_status_patch_failed",
                ref_key=normalized,
                status=status,
                error=str(exc),
            )

    def _schedule_memo_cache_refresh(self, memo_ref_key: str) -> None:
        asyncio.create_task(self._refresh_memo_caches(memo_ref_key))

    async def _refresh_memo_caches(self, memo_ref_key: str) -> None:
        try:
            await MeetingMemoCacheService().get_memo_detail(memo_ref_key, force_refresh=True)
        except Exception as exc:
            logger.warning(
                "meeting_memo_reject_detail_refresh_failed",
                ref_key=memo_ref_key,
                error=str(exc),
            )
        try:
            await MeetingDashboardCacheService().refresh_dashboard()
        except Exception as exc:
            logger.warning(
                "meeting_memo_reject_dashboard_refresh_failed",
                ref_key=memo_ref_key,
                error=str(exc),
            )

    async def find_rooms(
        self,
        payload: MeetingRoomsRequest,
        *,
        current_user: User,
    ) -> list[MeetingRoomRead]:
        await self._ensure_access(current_user)
        backend = self._backend()
        slot = {"start": payload.slot_start, "end": payload.slot_end or payload.slot_start}
        try:
            rooms = await backend.find_rooms(
                selected_slot=slot,
                room_name=payload.room_name,
                current_user=current_user,
            )
        except MeetingBackendError as exc:
            raise MeetingServiceError(str(exc)) from exc
        return [room_read(item) for item in rooms]

    async def preview_invite(
        self,
        payload: MeetingInvitePreviewRequest,
        *,
        current_user: User,
    ) -> MeetingInviteDraftRead:
        await self._ensure_access(current_user)
        backend = self._backend()
        memo = await self._load_memo_optional(
            backend,
            current_user=current_user,
            memo_ref_key=str(payload.memo_ref_key) if payload.memo_ref_key else None,
            memo_number=payload.memo_number,
        )
        participants = await backend.resolve_participants(
            payload.participant_fio or (memo.participant_fio if memo else []),
            current_user=current_user,
        )
        draft = await backend.prepare_invite(
            memo=memo,
            participants=participants,
            selected_slot={"start": payload.slot_start, "end": payload.slot_end},
            selected_room={"name": payload.room_name or ""},
            subject=payload.subject,
            current_user=current_user,
        )
        if draft is None:
            raise MeetingServiceError("Не удалось подготовить черновик приглашения")
        return invite_read(draft)

    async def send_invite(
        self,
        payload: MeetingInviteSendRequest,
        *,
        current_user: User,
    ) -> dict:
        await self._ensure_access(current_user)
        backend = self._backend()
        draft = InviteDraft(
            subject=payload.subject,
            start=payload.start,
            end=payload.end,
            location=payload.location,
            attendees=payload.attendees,
            body=payload.body,
        )
        try:
            result = await backend.send_invite(draft, current_user=current_user)
        except MeetingBackendError as exc:
            raise MeetingServiceError(str(exc)) from exc

        await self.audit.log(
            action="meeting.invite_sent",
            actor_id=current_user.id,
            resource_type="meeting_invite",
            payload={"subject": payload.subject, "start": payload.start, "attendees": payload.attendees},
        )

        if payload.memo_ref_key is not None:
            memo_detail: dict | None = None
            normalized_ref = str(payload.memo_ref_key).strip().lower()
            try:
                memo_detail, _, _ = await MeetingMemoCacheService().get_memo_detail(normalized_ref)
            except MemoCacheMissError:
                pass
            await MeetingRegistryService(self.db).upsert_from_invite(
                memo_ref_key=normalized_ref,
                slot_start=payload.start,
                slot_end=payload.end,
                subject=payload.subject,
                location=payload.location or None,
                attendees=payload.attendees,
                approved_by=current_user,
                memo_detail=memo_detail,
                sent_payload=result if isinstance(result, dict) else None,
            )

        return result

    async def list_registry(
        self,
        *,
        stage: str | None,
        current_user: User,
    ) -> MeetingRegistryRead:
        await self._ensure_access(current_user)
        registry = MeetingRegistryService(self.db)
        try:
            all_entries = await registry.list_entries(stage_filter="all")
            if stage and stage.strip().lower() not in {"", "all", "approved"}:
                entries = await registry.list_entries(stage_filter=stage)
            else:
                entries = all_entries
        except ValueError as exc:
            raise MeetingServiceError(str(exc)) from exc
        return MeetingRegistryRead(
            items=[registry_item_read(entry) for entry in entries],
            stage_counts=build_stage_counts(all_entries),
            fetched_at=datetime.now(UTC).isoformat(),
            error=None,
        )

    async def run(
        self,
        payload: MeetingRunCreate,
        *,
        current_user: User,
    ) -> MeetingRunRead:
        await self._ensure_access(current_user)
        agent = await self._get_agent_or_raise()
        input_payload = payload.model_dump(mode="json", exclude={"title"}, exclude_none=True)
        if payload.planned_start:
            input_payload["planned_start"] = payload.planned_start.isoformat()

        title = payload.title or _default_run_title(payload)
        task = Task(
            title=title,
            description=payload.initiator_comment,
            status=TaskStatus.PENDING,
            task_type="meeting",
            input_payload=input_payload,
            created_by_id=current_user.id,
            agent_id=agent.id,
            requires_human_review=True,
            task_metadata={"agent_slug": MEETING_AGENT_SLUG},
        )
        self.db.add(task)
        await self.db.flush()

        from app.workers.tasks import run_meeting_task

        async_result = run_meeting_task.apply_async(args=[str(task.id)], queue="agents")
        task.celery_task_id = async_result.id
        await self.db.flush()

        await self.audit.log(
            action="meeting.run_created",
            actor_id=current_user.id,
            resource_type="task",
            resource_id=str(task.id),
            payload={"memo_ref_key": input_payload.get("memo_ref_key"), "memo_number": payload.memo_number},
        )
        return MeetingRunRead(
            task_id=task.id,
            status=task.status.value,
            celery_task_id=task.celery_task_id,
            requires_human_review=task.requires_human_review,
        )

    async def get_run(
        self,
        task_id: uuid.UUID,
        *,
        current_user: User,
    ) -> MeetingRunResultRead:
        await self._ensure_access(current_user)
        if not await PermissionService(self.db).can_access_task(current_user, task_id):
            raise MeetingServiceError("Нет доступа к задаче")

        task = await self.db.get(Task, task_id)
        if task is None or task.task_type != "meeting":
            raise MeetingServiceError("Задача агента совещаний не найдена")

        result = await TaskService(self.db).get_current_result(task_id)
        result_payload = None
        if result is not None:
            result_payload = result.raw_output or {
                "summary": result.summary,
                "status": result.status,
                "additional_data": result.additional_data,
            }
        elif task.final_result:
            result_payload = task.final_result

        return MeetingRunResultRead(
            task_id=task.id,
            status=task.status.value,
            summary=(result.summary if result else None) or _result_summary(task.final_result),
            result=result_payload,
            requires_human_review=task.requires_human_review,
            error_message=task.error_message,
        )

    async def execute_task(self, task_id: uuid.UUID) -> dict:
        task = await self.db.get(Task, task_id)
        if task is None:
            raise MeetingServiceError("Задача не найдена")
        if task.created_by_id is None:
            raise MeetingServiceError("У задачи не указан инициатор")

        user = await self.db.get(User, task.created_by_id)
        if user is None:
            raise MeetingServiceError("Инициатор задачи не найден")

        task.status = TaskStatus.RUNNING
        task.started_at = datetime.now(UTC)
        task.error_message = None
        await self.db.flush()

        backend = MeetingBackend(self.db, agent_id=task.agent_id, task_id=task.id)
        payload = dict(task.input_payload or {})
        payload["task_id"] = str(task.id)
        payload["user_id"] = str(user.id)

        try:
            result = await backend.run_agent(payload, current_user=user)
            dumped = result.model_dump(mode="json")
            task.status = (
                TaskStatus.WAITING_HUMAN if result.requires_human_review else TaskStatus.COMPLETED
            )
            task.requires_human_review = result.requires_human_review
            task.final_result = dumped
            task.finished_at = datetime.now(UTC)

            await TaskService(self.db).save_result(
                task,
                TaskResultCreate(
                    agent_id=task.agent_id,
                    status=result.status,
                    summary=result.summary,
                    findings=[item.model_dump(mode="json") for item in result.findings],
                    data_confidence=result.data_confidence,
                    requires_human_review=result.requires_human_review,
                    additional_data={
                        "memo": dumped.get("memo"),
                        "invite_draft": dumped.get("invite_draft"),
                        "selected_slot": dumped.get("selected_slot"),
                        "selected_room": dumped.get("selected_room"),
                    },
                    raw_output=dumped,
                ),
            )
            await self.audit.log(
                action="meeting.run_completed",
                actor_id=user.id,
                resource_type="task",
                resource_id=str(task.id),
                payload={"status": result.status},
            )
            return dumped
        except Exception as exc:  # noqa: BLE001
            task.status = TaskStatus.FAILED
            task.error_message = str(exc)
            task.finished_at = datetime.now(UTC)
            await self.audit.log(
                action="meeting.run_failed",
                actor_id=user.id,
                resource_type="task",
                resource_id=str(task.id),
                payload={"error": str(exc)},
            )
            raise

    async def _ensure_access(self, user: User) -> None:
        if not await can_access_meeting_agent(self.db, user):
            raise MeetingServiceError("Нет доступа к агенту совещаний")

    async def _get_agent_or_raise(self) -> Agent:
        agent = await self.db.scalar(select(Agent).where(Agent.slug == MEETING_AGENT_SLUG))
        if agent is None:
            raise MeetingServiceError(
                f"Агент «{AGENT_NAME}» не зарегистрирован в БД. Выполните seed_meeting_agent_rbac."
            )
        return agent

    def _backend(self, *, agent_id: uuid.UUID | None = None, task_id: uuid.UUID | None = None) -> MeetingBackend:
        return MeetingBackend(self.db, agent_id=agent_id, task_id=task_id)

    async def _load_memo_optional(
        self,
        backend: MeetingBackend,
        *,
        current_user: User,
        memo_ref_key: str | None,
        memo_number: str | None,
    ) -> MeetingMemo | None:
        if not memo_ref_key and not memo_number:
            return None
        try:
            return await backend.load_memo(
                memo_ref_key=memo_ref_key,
                memo_number=memo_number,
                current_user=current_user,
            )
        except MeetingBackendError as exc:
            raise MeetingServiceError(str(exc)) from exc


def _user_fio(user: User) -> str | None:
    full_name = getattr(user, "full_name", None)
    if isinstance(full_name, str) and full_name.strip():
        return full_name.strip()
    parts = [
        getattr(user, "last_name", None),
        getattr(user, "first_name", None),
        getattr(user, "middle_name", None),
    ]
    name = " ".join(part.strip() for part in parts if isinstance(part, str) and part.strip())
    return name or None


def _default_run_title(payload: MeetingRunCreate) -> str:
    if payload.memo_number:
        return f"Совещание по СЗ {payload.memo_number}"
    if payload.memo_ref_key:
        return f"Совещание по СЗ {payload.memo_ref_key}"
    return "Организация совещания"


def _result_summary(final_result: dict | None) -> str | None:
    if not final_result:
        return None
    summary = final_result.get("summary")
    return str(summary) if summary else None
