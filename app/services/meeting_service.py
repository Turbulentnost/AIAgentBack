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
    registry_cancel_read,
    registry_history_read,
    registry_participants_read,
    room_read,
    slot_read,
)
from app.agents.meeting_agent.config import AGENT_NAME
from app.models.agent import Agent
from app.models.enums import MeetingRegistryStage, TaskStatus
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
    MeetingRegistryCancelRead,
    MeetingRegistryCancelRequest,
    MeetingRegistryParticipantsRead,
    MeetingRegistryParticipantSearchRead,
    MeetingRegistryHistoryRead,
    MeetingRegistryParticipantsApplyRead,
    MeetingRegistryParticipantsApplyRequest,
    MeetingRegistryParticipantsRemovalConfirmRead,
    MeetingRegistryParticipantsRemovalConfirmRequest,
    MeetingRegistryRescheduleApproveRead,
    MeetingRegistryRescheduleApproveRequest,
    MeetingRegistryRescheduleSlotPreviewRead,
    MeetingRegistryRescheduleSlotPreviewRequest,
    MeetingRegistryStageRead,
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
    collect_attendees_from_registry_entry,
    registry_participant_names,
)
from app.services.meeting_offline_cache import (
    build_offline_approve_result,
    is_offline_cache_detail,
)
from app.services.meeting_dashboard_cache import MeetingDashboardCacheService
from app.services.meeting_permission import MEETING_AGENT_SLUG, can_access_meeting_agent
from app.services.meeting_registry_service import (
    MeetingRegistryService,
    _pending_removal_from_entry,
    build_stage_counts,
    participant_names_diff,
)
from app.services.meeting_registry_slot import suggest_earlier_slots_after_removal
from app.core.logging import get_logger
from app.services.meeting_duration import resolve_duration_minutes
from app.services.meeting_invite_format import (
    format_invite_location_from_detail,
    resolve_invite_subject,
    resolve_room_for_location,
)
from app.services.meeting_slot import (
    format_planned_start_for_search,
    format_search_start_after_registry_slot,
    format_search_start_from_meeting_date,
    format_slot_label,
    parse_slot_datetime,
    slot_duration_minutes,
)
from app.services.permission_service import PermissionService
from app.services.task_service import TaskService
from app.tools.Outlook.find_meeting_slot import build_slot_participant_details
from app.tools.Outlook.send_meeting_invite import dispatch_meeting_invite, load_config
from app.tools.Outlook.reschedule_meeting import dispatch_reschedule_meeting
from app.tools.Outlook.cancel_meeting import dispatch_cancel_meeting
from app.tools.Outlook.update_meeting_attendees import dispatch_update_meeting_attendees
from app.tools.onec.approve_service_memo import approve_service_memo
from app.tools.onec.reject_service_memo import reject_service_memo
from app.services.meeting_memo_cache import (
    MeetingMemoCacheService,
    MemoCacheMissError,
    detail_to_memo_document,
)
from app.tools.onec.service_memo_shared import APPROVED_STATUS, ServiceMemoWorkflowError

logger = get_logger(__name__)

RESCHEDULABLE_REGISTRY_STAGES = frozenset(
    {
        MeetingRegistryStage.INVITATIONS_SENT,
        MeetingRegistryStage.CANCELLED,
    }
)


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
            attendee_details=attendee_details,
        )

        await self._sync_approved_status_after_invite(
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
        use_offline_approve = cached is not None and is_offline_cache_detail(cached["payload"])

        if use_offline_approve:
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
                if cached is not None and any(
                    token in lowered
                    for token in ("401", "403", "404", "timeout", "connection", "connect", "odata", "500")
                ):
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
                            else "Согласована офлайн (кэш Redis)"
                        )
                        await cache_service.patch_status(
                            normalized_ref,
                            APPROVED_STATUS,
                            history_message=history_message,
                        )
                        await self._apply_memo_status_to_cache(normalized_ref, APPROVED_STATUS)
                elif any(
                    token in lowered
                    for token in ("401", "403", "404", "timeout", "connection", "connect", "odata")
                ):
                    raise MeetingServiceError(format_onec_load_error(exc), status_code=503) from exc
                else:
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
                "offline_cache": use_offline_approve,
            },
        )

        return MeetingMemoApproveRead.model_validate(raw)

    async def _sync_approved_status_after_invite(
        self,
        memo_ref_key: str,
        *,
        memo_detail: dict | None,
        approver_fio: str | None,
    ) -> None:
        """После отправки приглашения переводит СЗ в «Согласована» в Redis-кэше."""
        normalized = memo_ref_key.strip().lower()
        detail = memo_detail
        if detail is None:
            cached = await MeetingMemoCacheService().read_cached(normalized)
            detail = cached["payload"] if cached else None
        if detail is None:
            return
        if str(detail.get("status") or "") == APPROVED_STATUS:
            await self._apply_memo_status_to_cache(normalized, APPROVED_STATUS)
            return

        history_message = (
            f"Согласована при отправке приглашения ({approver_fio})"
            if approver_fio
            else "Согласована при отправке приглашения"
        )
        await MeetingMemoCacheService().patch_status(
            normalized,
            APPROVED_STATUS,
            history_message=history_message,
        )
        await self._apply_memo_status_to_cache(normalized, APPROVED_STATUS)

    async def _sync_offline_cache_after_invite(
        self,
        memo_ref_key: str,
        *,
        memo_detail: dict | None,
        approver_fio: str | None,
    ) -> None:
        await self._sync_approved_status_after_invite(
            memo_ref_key,
            memo_detail=memo_detail,
            approver_fio=approver_fio,
        )

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

    async def get_registry_participants(
        self,
        memo_ref_key: str,
        *,
        current_user: User,
    ) -> MeetingRegistryParticipantsRead:
        await self._ensure_access(current_user)
        normalized_ref = memo_ref_key.strip().lower()
        entry = await MeetingRegistryService(self.db).get_entry(normalized_ref)
        if entry is None:
            raise MeetingServiceError("Совещание не найдено в реестре", status_code=404)

        return registry_participants_read(entry)

    async def search_registry_participant(
        self,
        memo_ref_key: str,
        fio: str,
        *,
        current_user: User,
    ) -> MeetingRegistryParticipantSearchRead:
        """Поиск участника по ФИО в Exchange GAL для модалки добавления."""
        await self._ensure_access(current_user)
        query = fio.strip()
        normalized_ref = memo_ref_key.strip().lower()
        entry = await MeetingRegistryService(self.db).get_entry(normalized_ref)
        if entry is None:
            raise MeetingServiceError("Совещание не найдено в реестре", status_code=404)

        current_names = {name.casefold() for name in registry_participant_names(entry)}
        if not query:
            return MeetingRegistryParticipantSearchRead(
                query=query,
                fio=query,
                already_added=False,
                can_add=False,
            )

        already_added = query.casefold() in current_names
        backend = self._backend()
        try:
            resolved = await backend.resolve_participants([query], current_user=current_user)
        except MeetingBackendError as exc:
            raise MeetingServiceError(str(exc)) from exc

        match = resolved[0] if resolved else None
        found = bool(match and match.found and match.email)
        return MeetingRegistryParticipantSearchRead(
            query=query,
            fio=match.fio if match else query,
            email=match.email if found and match else None,
            found=found,
            already_added=already_added,
            can_add=found and not already_added,
        )

    async def cancel_registry_participants_removal(
        self,
        memo_ref_key: str,
        *,
        current_user: User,
    ) -> MeetingRegistryParticipantsRead:
        """Сбрасывает черновик pending_removal без изменения состава в БД и Outlook."""
        await self._ensure_access(current_user)
        normalized_ref = memo_ref_key.strip().lower()
        registry = MeetingRegistryService(self.db)
        entry = await registry.get_entry(normalized_ref)
        if entry is None:
            raise MeetingServiceError("Совещание не найдено в реестре", status_code=404)

        pending = _pending_removal_from_entry(entry)
        if pending:
            entry = await registry.clear_pending_removal(normalized_ref)
            await self.audit.log(
                action="meeting.registry_participants_removal_cancelled",
                actor_id=current_user.id,
                resource_type="meeting_registry",
                resource_id=normalized_ref,
                payload={
                    "removed": list(pending.get("removed") or []),
                },
            )

        if entry is None:
            entry = await registry.get_entry(normalized_ref)
        if entry is None:
            raise MeetingServiceError("Совещание не найдено в реестре", status_code=404)

        return registry_participants_read(entry)

    async def get_registry_history(
        self,
        memo_ref_key: str,
        *,
        current_user: User,
    ) -> MeetingRegistryHistoryRead:
        await self._ensure_access(current_user)
        normalized_ref = memo_ref_key.strip().lower()
        registry = MeetingRegistryService(self.db)
        entry = await registry.get_entry(normalized_ref)
        if entry is None:
            raise MeetingServiceError("Совещание не найдено в реестре", status_code=404)

        events = await registry.list_events(normalized_ref)
        return registry_history_read(
            entry,
            events,
            fetched_at=datetime.now(UTC).isoformat(),
        )

    async def apply_registry_participants(
        self,
        memo_ref_key: str,
        payload: MeetingRegistryParticipantsApplyRequest,
        *,
        current_user: User,
    ) -> MeetingRegistryParticipantsApplyRead:
        await self._ensure_access(current_user)
        normalized_ref = memo_ref_key.strip().lower()
        registry = MeetingRegistryService(self.db)
        entry = await registry.get_entry(normalized_ref)
        if entry is None:
            raise MeetingServiceError("Совещание не найдено в реестре", status_code=404)
        if entry.stage == MeetingRegistryStage.CANCELLED:
            raise MeetingServiceError(
                "Нельзя изменить участников отменённого совещания",
                status_code=400,
            )

        current_names = entry.participants if isinstance(entry.participants, list) else []
        target_names = payload.participants
        added, removed = participant_names_diff(current_names, target_names)
        if not added and not removed:
            raise MeetingServiceError("Список участников не изменился", status_code=400)

        backend = self._backend()
        fio_to_resolve = list(dict.fromkeys([*added, *removed]))
        try:
            resolved = await backend.resolve_participants(
                fio_to_resolve,
                current_user=current_user,
            )
        except MeetingBackendError as exc:
            raise MeetingServiceError(str(exc)) from exc

        by_fio = {item.fio.casefold(): item for item in resolved}
        missing_added = [
            fio for fio in added if not (by_fio.get(fio.casefold()) and by_fio[fio.casefold()].email)
        ]
        if missing_added:
            raise MeetingServiceError(
                format_missing_emails_error(missing_added),
                status_code=400,
            )

        add_emails = [
            by_fio[fio.casefold()].email
            for fio in added
            if by_fio.get(fio.casefold()) and by_fio[fio.casefold()].email
        ]
        remove_emails = [
            by_fio[fio.casefold()].email
            for fio in removed
            if by_fio.get(fio.casefold()) and by_fio[fio.casefold()].email
        ]

        payload_attendees = list((entry.payload or {}).get("attendees") or [])
        remove_keys = {email.lower() for email in remove_emails}
        new_attendees = [email for email in payload_attendees if email.lower() not in remove_keys]
        existing_keys = {email.lower() for email in new_attendees}
        for email in add_emails:
            key = email.lower()
            if key not in existing_keys:
                new_attendees.append(email)
                existing_keys.add(key)

        fetched_at = (
            entry.updated_at.isoformat()
            if entry.updated_at
            else entry.invitations_sent_at.isoformat()
        )

        removal_only = bool(removed) and not added
        earlier_slot_suggestion = None
        if removal_only:
            if entry.slot_start is not None:
                memo_detail: dict | None = None
                try:
                    memo_detail, _, _ = await MeetingMemoCacheService().get_memo_detail(normalized_ref)
                except MemoCacheMissError:
                    pass
                earlier_slot_suggestion = await suggest_earlier_slots_after_removal(
                    entry=entry,
                    remaining_attendee_emails=new_attendees,
                    memo_detail=memo_detail,
                    current_user=current_user,
                    backend=backend,
                )
            await registry.save_pending_removal(
                normalized_ref,
                participants=target_names,
                attendees=new_attendees,
                removed=removed,
            )
            return MeetingRegistryParticipantsApplyRead(
                ref_key=normalized_ref,
                participants=target_names,
                participants_count=len(target_names),
                added=added,
                removed=removed,
                outlook_updated=False,
                earlier_slot_suggestion=earlier_slot_suggestion,
                pending_confirmation=True,
                fetched_at=fetched_at,
            )

        apply_message = (payload.message or "").strip() or "Состав участников совещания изменён"
        outlook_updated = False
        outlook_warning: str | None = None
        outlook_payload: dict[str, Any] | None = None

        if (add_emails or remove_emails) and (
            entry.outlook_item_id or (entry.subject and entry.slot_start)
        ):
            kwargs: dict[str, Any] = {
                "add": add_emails,
                "remove": remove_emails,
                "message": apply_message,
            }
            if entry.outlook_item_id:
                kwargs["item_id"] = entry.outlook_item_id
                kwargs["changekey"] = entry.outlook_changekey or ""
            else:
                start_label = self._format_registry_slot_start(entry.slot_start)
                kwargs["subject"] = entry.subject or ""
                kwargs["start"] = start_label or entry.slot_start.isoformat()
            try:
                outlook_payload = await asyncio.to_thread(
                    dispatch_update_meeting_attendees,
                    **kwargs,
                )
                outlook_updated = outlook_payload.get("status") == "updated"
            except Exception as exc:
                raise MeetingServiceError(
                    f"Не удалось обновить участников в Outlook/Exchange: {exc}"
                ) from exc
        elif add_emails or remove_emails:
            outlook_warning = (
                "Совещание не привязано к Outlook. Список участников обновлён только в реестре."
            )

        try:
            entry = await registry.apply_participants_update(
                normalized_ref,
                participants=target_names,
                attendees=new_attendees,
                updated_by=current_user,
                apply_message=apply_message,
                outlook_payload=outlook_payload,
            )
        except ValueError as exc:
            raise MeetingServiceError(str(exc), status_code=400) from exc

        await self.audit.log(
            action="meeting.registry_participants_updated",
            actor_id=current_user.id,
            resource_type="meeting_registry",
            resource_id=normalized_ref,
            payload={
                "subject": entry.subject,
                "added": added,
                "removed": removed,
                "participants_count": entry.participants_count,
                "outlook_updated": outlook_updated,
            },
        )

        fetched_at = (
            entry.updated_at.isoformat()
            if entry.updated_at
            else entry.invitations_sent_at.isoformat()
        )

        return MeetingRegistryParticipantsApplyRead(
            ref_key=normalized_ref,
            participants=list(entry.participants or []),
            participants_count=int(entry.participants_count or 0),
            added=added,
            removed=removed,
            outlook_updated=outlook_updated,
            outlook_warning=outlook_warning,
            message=apply_message or None,
            earlier_slot_suggestion=earlier_slot_suggestion,
            pending_confirmation=False,
            fetched_at=fetched_at,
        )

    async def confirm_registry_participants_removal(
        self,
        memo_ref_key: str,
        payload: MeetingRegistryParticipantsRemovalConfirmRequest,
        *,
        current_user: User,
    ) -> MeetingRegistryParticipantsRemovalConfirmRead:
        await self._ensure_access(current_user)
        normalized_ref = memo_ref_key.strip().lower()
        registry = MeetingRegistryService(self.db)
        entry = await registry.get_entry(normalized_ref)
        if entry is None:
            raise MeetingServiceError("Совещание не найдено в реестре", status_code=404)
        if entry.stage == MeetingRegistryStage.CANCELLED:
            raise MeetingServiceError(
                "Нельзя изменить участников отменённого совещания",
                status_code=400,
            )
        if entry.slot_start is None:
            raise MeetingServiceError(
                "У совещания не указано время для переноса",
                status_code=400,
            )

        pending = _pending_removal_from_entry(entry)
        if pending:
            target_names = list(pending.get("participants") or payload.participants)
            removed = list(pending.get("removed") or payload.removed)
            pending_attendees = pending.get("attendees")
            if not removed:
                raise MeetingServiceError(
                    "Подтверждение доступно только для сценария удаления участников",
                    status_code=400,
                )
            if {name.casefold() for name in removed} != {
                name.casefold() for name in payload.removed
            }:
                raise MeetingServiceError(
                    "Список удалённых участников не совпадает с текущим состоянием совещания",
                    status_code=400,
                )
        else:
            current_names = entry.participants if isinstance(entry.participants, list) else []
            target_names = payload.participants
            added, removed = participant_names_diff(current_names, target_names)
            if added or not removed:
                raise MeetingServiceError(
                    "Подтверждение доступно только для сценария удаления участников",
                    status_code=400,
                )
            if {name.casefold() for name in removed} != {name.casefold() for name in payload.removed}:
                raise MeetingServiceError(
                    "Список удалённых участников не совпадает с текущим состоянием совещания",
                    status_code=400,
                )
            pending_attendees = None

        backend = self._backend()
        try:
            resolved = await backend.resolve_participants(
                removed,
                current_user=current_user,
            )
        except MeetingBackendError as exc:
            raise MeetingServiceError(str(exc)) from exc

        by_fio = {item.fio.casefold(): item for item in resolved}
        remove_emails = [
            by_fio[fio.casefold()].email
            for fio in removed
            if by_fio.get(fio.casefold()) and by_fio[fio.casefold()].email
        ]

        if isinstance(pending_attendees, list) and pending_attendees:
            new_attendees = [email for email in pending_attendees if email]
        else:
            payload_attendees = list((entry.payload or {}).get("attendees") or [])
            remove_keys = {email.lower() for email in remove_emails}
            new_attendees = [
                email for email in payload_attendees if email.lower() not in remove_keys
            ]
        if not new_attendees:
            raise MeetingServiceError(
                "После удаления участников должен остаться хотя бы один участник",
                status_code=400,
            )

        previous_label = format_slot_label(
            entry.slot_start.isoformat(),
            entry.slot_end.isoformat() if entry.slot_end else entry.slot_start.isoformat(),
        )
        composition_message = "Состав участников совещания изменён"
        reschedule_message = (payload.message or "").strip() or "Совещание перенесено"

        memo_detail: dict | None = None
        try:
            memo_detail, _, _ = await MeetingMemoCacheService().get_memo_detail(normalized_ref)
        except MemoCacheMissError:
            pass

        subject = resolve_invite_subject(memo_detail) or entry.subject or "Совещание"
        location = format_invite_location_from_detail(memo_detail) or entry.location
        duration = slot_duration_minutes(payload.slot_start, payload.slot_end)

        outlook_updated = False
        attendee_outlook_payload: dict[str, Any] | None = None
        reschedule_outlook_payload: dict[str, Any] | None = None

        if entry.outlook_item_id or (entry.subject and entry.slot_start):
            if remove_emails and entry.outlook_item_id:
                try:
                    attendee_outlook_payload = await asyncio.to_thread(
                        dispatch_update_meeting_attendees,
                        item_id=entry.outlook_item_id or "",
                        changekey=entry.outlook_changekey or "",
                        remove=remove_emails,
                        message=composition_message,
                    )
                except Exception as exc:
                    raise MeetingServiceError(
                        f"Не удалось обновить участников в Outlook/Exchange: {exc}"
                    ) from exc

            if entry.outlook_item_id:
                try:
                    reschedule_outlook_payload = await asyncio.to_thread(
                        dispatch_reschedule_meeting,
                        item_id=entry.outlook_item_id or "",
                        changekey=entry.outlook_changekey or "",
                        new_start=payload.slot_start,
                        new_end=payload.slot_end,
                        duration_minutes=duration,
                        location=location,
                        message=reschedule_message,
                    )
                    outlook_updated = reschedule_outlook_payload.get("status") == "rescheduled"
                except Exception as exc:
                    raise MeetingServiceError(
                        f"Не удалось перенести совещание в Outlook/Exchange: {exc}"
                    ) from exc
            elif remove_emails:
                start_label = self._format_registry_slot_start(entry.slot_start)
                try:
                    attendee_outlook_payload = await asyncio.to_thread(
                        dispatch_update_meeting_attendees,
                        subject=entry.subject or "",
                        start=start_label or entry.slot_start.isoformat(),
                        remove=remove_emails,
                        message=composition_message,
                    )
                    outlook_updated = attendee_outlook_payload.get("status") == "updated"
                except Exception as exc:
                    raise MeetingServiceError(
                        f"Не удалось обновить участников в Outlook/Exchange: {exc}"
                    ) from exc

        entry = await registry.apply_participants_update(
            normalized_ref,
            participants=target_names,
            attendees=new_attendees,
            updated_by=current_user,
            apply_message=composition_message,
            outlook_payload=attendee_outlook_payload,
        )
        entry = await registry.apply_reschedule(
            memo_ref_key=normalized_ref,
            slot_start=payload.slot_start,
            slot_end=payload.slot_end,
            subject=subject,
            location=location or None,
            attendees=new_attendees,
            rescheduled_by=current_user,
            sent_payload=reschedule_outlook_payload if isinstance(reschedule_outlook_payload, dict) else None,
            reschedule_message=reschedule_message,
            participant_names=target_names,
            memo_detail=memo_detail,
        )
        await registry.clear_pending_removal(normalized_ref)

        await self._sync_meeting_slot_to_cache(
            normalized_ref,
            slot_start=payload.slot_start,
            slot_end=payload.slot_end,
            location=location or None,
            history_message=(
                f"Совещание перенесено на {format_slot_label(payload.slot_start, payload.slot_end)}"
            ),
        )

        await self.audit.log(
            action="meeting.registry_participants_removal_confirmed",
            actor_id=current_user.id,
            resource_type="meeting_registry",
            resource_id=normalized_ref,
            payload={
                "subject": entry.subject,
                "removed": removed,
                "participants_count": entry.participants_count,
                "slot_start": payload.slot_start,
                "slot_end": payload.slot_end,
                "outlook_updated": outlook_updated,
            },
        )

        fetched_at = (
            entry.updated_at.isoformat()
            if entry.updated_at
            else entry.invitations_sent_at.isoformat()
        )

        return MeetingRegistryParticipantsRemovalConfirmRead(
            ref_key=normalized_ref,
            participants=list(entry.participants or []),
            participants_count=int(entry.participants_count or 0),
            removed=removed,
            previous_slot_label=previous_label,
            slot_label=format_slot_label(payload.slot_start, payload.slot_end),
            slot_start=payload.slot_start,
            slot_end=payload.slot_end,
            outlook_updated=outlook_updated,
            message=reschedule_message,
            fetched_at=fetched_at,
        )

    @staticmethod
    def _outlook_cancel_not_found(exc: BaseException) -> bool:
        message = str(exc).lower()
        return "не найдено" in message or "not found" in message

    @staticmethod
    def _format_registry_slot_start(slot_start: Any) -> str:
        if slot_start is None:
            return ""
        if hasattr(slot_start, "strftime"):
            return slot_start.strftime("%Y-%m-%d %H:%M")
        return str(slot_start)

    async def _resolve_registry_entry_recipients(
        self,
        entry: Any,
        *,
        backend: MeetingBackend,
        current_user: User,
    ) -> tuple[list[MeetingAttendeeRead], list[ResolvedParticipant]]:
        """Участники для операций реестра: только ФИО из БД и e-mail через lookup."""
        specs = collect_attendees_from_registry_entry(entry)
        if not specs:
            raise MeetingServiceError(
                "В реестре не указаны участники совещания",
                status_code=400,
            )

        resolved_lookup = await backend.resolve_participants(
            [fio for fio, _role in specs],
            current_user=current_user,
        )
        by_fio = {item.fio.casefold(): item for item in resolved_lookup}

        attendee_reads: list[MeetingAttendeeRead] = []
        resolved: list[ResolvedParticipant] = []
        missing: list[str] = []
        for fio, priority_role in specs:
            match = by_fio.get(fio.casefold())
            email = match.email if match and match.email else None
            found = bool(email)
            if not found:
                missing.append(fio)
            else:
                resolved.append(ResolvedParticipant(fio=fio, email=email, found=True))
            attendee_reads.append(
                MeetingAttendeeRead(
                    fio=fio,
                    email=email,
                    role=priority_role,
                    role_label=priority_role_label(priority_role),
                    weight=weight_for_priority_role(priority_role, None),
                    required_for_slot=found,
                    found=found,
                )
            )

        if missing or not resolved:
            raise MeetingServiceError(
                format_missing_emails_error(missing or [fio for fio, _ in specs]),
                status_code=400,
            )
        return attendee_reads, resolved

    async def _cancel_outlook_for_registry_entry(
        self,
        entry: Any,
        message: str,
    ) -> tuple[dict[str, Any] | None, bool, str | None]:
        attempts: list[dict[str, Any]] = []
        if entry.outlook_item_id:
            attempts.append(
                {
                    "item_id": entry.outlook_item_id,
                    "changekey": entry.outlook_changekey or "",
                    "message": message,
                }
            )
        if entry.subject and entry.slot_start:
            start_label = self._format_registry_slot_start(entry.slot_start)
            attempts.extend(
                [
                    {
                        "subject": entry.subject,
                        "start": start_label or entry.slot_start.isoformat(),
                        "message": message,
                        "tolerance_minutes": 5,
                        "match_mode": "exact",
                    },
                    {
                        "subject": entry.subject,
                        "start": start_label or entry.slot_start.isoformat(),
                        "message": message,
                        "tolerance_minutes": 180,
                        "match_mode": "exact",
                    },
                    {
                        "subject": entry.subject,
                        "start": start_label or entry.slot_start.isoformat(),
                        "message": message,
                        "match_mode": "day",
                    },
                ]
            )

        last_error: Exception | None = None
        for kwargs in attempts:
            try:
                cancel_payload = await asyncio.to_thread(dispatch_cancel_meeting, **kwargs)
                outlook_cancelled = cancel_payload.get("status") == "cancelled"
                return cancel_payload, outlook_cancelled, None
            except RuntimeError as exc:
                if "уже отменено" in str(exc).lower():
                    return None, True, None
                last_error = exc
                if not self._outlook_cancel_not_found(exc):
                    raise MeetingServiceError(
                        f"Не удалось отменить совещание в Outlook/Exchange: {exc}"
                    ) from exc
            except Exception as exc:
                last_error = exc
                if not self._outlook_cancel_not_found(exc):
                    raise MeetingServiceError(
                        f"Не удалось отменить совещание в Outlook/Exchange: {exc}"
                    ) from exc

        if last_error is not None and self._outlook_cancel_not_found(last_error):
            warning = (
                "Совещание не найдено в Outlook/Exchange. "
                "Статус в реестре будет обновлён на «Отменено»."
            )
            return {"status": "not_found", "error": str(last_error)}, False, warning

        if last_error is not None:
            raise MeetingServiceError(
                f"Не удалось отменить совещание в Outlook/Exchange: {last_error}"
            ) from last_error
        return None, False, None

    async def cancel_registry_meeting(
        self,
        memo_ref_key: str,
        payload: MeetingRegistryCancelRequest,
        *,
        current_user: User,
    ) -> MeetingRegistryCancelRead:
        await self._ensure_access(current_user)
        normalized_ref = memo_ref_key.strip().lower()
        registry = MeetingRegistryService(self.db)
        entry = await registry.get_entry(normalized_ref)
        if entry is None:
            raise MeetingServiceError("Совещание не найдено в реестре", status_code=404)

        if entry.stage == MeetingRegistryStage.CANCELLED:
            events = await registry.list_events(normalized_ref)
            outlook_cancelled = False
            for event in reversed(events):
                if event.event_type.value == "cancelled":
                    outlook_cancelled = bool((event.payload or {}).get("outlook_cancelled"))
                    break
            return registry_cancel_read(
                entry,
                outlook_cancelled=outlook_cancelled,
                outlook_warning=None,
                message=payload.message or None,
            )

        cancel_payload: dict[str, Any] | None = None
        outlook_cancelled = False
        outlook_warning: str | None = None
        cancel_message = (payload.message or "").strip()

        if entry.outlook_item_id or (entry.subject and entry.slot_start):
            cancel_payload, outlook_cancelled, outlook_warning = (
                await self._cancel_outlook_for_registry_entry(entry, cancel_message)
            )

        entry = await registry.mark_cancelled(
            memo_ref_key=normalized_ref,
            cancelled_by=current_user,
            message=cancel_message or None,
            cancel_payload=cancel_payload,
            outlook_cancelled=outlook_cancelled,
        )

        await self.audit.log(
            action="meeting.registry_cancelled",
            actor_id=current_user.id,
            resource_type="meeting_registry",
            resource_id=normalized_ref,
            payload={
                "subject": entry.subject,
                "outlook_cancelled": outlook_cancelled,
                "message": cancel_message or None,
            },
        )

        return registry_cancel_read(
            entry,
            outlook_cancelled=outlook_cancelled,
            outlook_warning=outlook_warning,
            message=cancel_message or None,
        )

    async def suggest_registry_reschedule_slot(
        self,
        memo_ref_key: str,
        payload: MeetingRegistryRescheduleSlotPreviewRequest,
        *,
        current_user: User,
    ) -> MeetingRegistryRescheduleSlotPreviewRead:
        await self._ensure_access(current_user)
        normalized_ref = memo_ref_key.strip().lower()
        registry = MeetingRegistryService(self.db)
        entry = await registry.get_entry(normalized_ref)
        if entry is None:
            raise MeetingServiceError("Совещание не найдено в реестре", status_code=404)
        if entry.stage not in RESCHEDULABLE_REGISTRY_STAGES:
            raise MeetingServiceError(
                "Перенос доступен только для совещаний с отправленными приглашениями "
                "или отменённых записей",
                status_code=400,
            )

        search_after = format_search_start_after_registry_slot(entry.slot_start, entry.slot_end)
        if not search_after:
            raise MeetingServiceError(
                "У совещения не указана дата/время для поиска нового слота",
                status_code=400,
            )

        duration = payload.duration_minutes
        if duration is None and entry.slot_start and entry.slot_end:
            duration = max(
                int((entry.slot_end - entry.slot_start).total_seconds() // 60),
                1,
            )

        previous_start = entry.slot_start.isoformat() if entry.slot_start else None
        previous_end = entry.slot_end.isoformat() if entry.slot_end else None
        previous_label = (
            format_slot_label(previous_start, previous_end)
            if previous_start and previous_end
            else None
        )

        attendee_specs = collect_attendees_from_registry_entry(entry)

        slot_preview = await self._slot_service().suggest_agent_slot_safe(
            normalized_ref,
            MeetingAgentSlotPreviewRequest(
                duration_minutes=duration,
                planned_start=search_after,
                search_start=search_after,
            ),
            current_user=current_user,
            attendee_specs=attendee_specs,
        )

        return MeetingRegistryRescheduleSlotPreviewRead(
            ref_key=normalized_ref,
            stage=MeetingRegistryStageRead(entry.stage.value),
            previous_slot_start=previous_start,
            previous_slot_end=previous_end,
            previous_slot_label=previous_label,
            search_after=search_after,
            slot_preview=slot_preview,
        )

    async def approve_registry_reschedule(
        self,
        memo_ref_key: str,
        payload: MeetingRegistryRescheduleApproveRequest,
        *,
        current_user: User,
    ) -> MeetingRegistryRescheduleApproveRead:
        await self._ensure_access(current_user)
        normalized_ref = memo_ref_key.strip().lower()
        registry = MeetingRegistryService(self.db)
        entry = await registry.get_entry(normalized_ref)
        if entry is None:
            raise MeetingServiceError("Совещание не найдено в реестре", status_code=404)
        if entry.stage not in RESCHEDULABLE_REGISTRY_STAGES:
            raise MeetingServiceError(
                "Перенос доступен только для совещаний с отправленными приглашениями "
                "или отменённых записей",
                status_code=400,
            )

        previous_label = None
        if entry.slot_start and entry.slot_end:
            previous_label = format_slot_label(
                entry.slot_start.isoformat(),
                entry.slot_end.isoformat(),
            )

        memo_detail: dict | None = None
        try:
            memo_detail, _, _ = await MeetingMemoCacheService().get_memo_detail(normalized_ref)
        except MemoCacheMissError:
            pass

        backend = self._backend()
        try:
            attendee_details, resolved = await self._resolve_registry_entry_recipients(
                entry,
                backend=backend,
                current_user=current_user,
            )
        except MeetingBackendError as exc:
            raise MeetingServiceError(str(exc)) from exc

        emails = [item.email for item in resolved if item.email]
        if not emails:
            raise MeetingServiceError("Не указаны e-mail участников для переноса совещания")

        subject = resolve_invite_subject(memo_detail, override=payload.subject) or entry.subject or "Совещание"
        location = format_invite_location_from_detail(memo_detail, override=payload.location) or entry.location
        duration = slot_duration_minutes(payload.slot_start, payload.slot_end)
        room = resolve_room_for_location(location)
        body = build_approve_invite_body(attendee_details, room=room)
        resources = [room["email"]] if room and room.get("email") else []
        reschedule_message = (payload.message or "").strip() or "Совещание перенесено"

        outlook_updated = False
        new_invite_sent = False
        sent_payload: dict[str, Any] | None = None
        can_reschedule_existing = (
            entry.stage == MeetingRegistryStage.INVITATIONS_SENT and bool(entry.outlook_item_id)
        )

        if can_reschedule_existing:
            try:
                sent_payload = await asyncio.to_thread(
                    dispatch_reschedule_meeting,
                    item_id=entry.outlook_item_id or "",
                    changekey=entry.outlook_changekey or "",
                    new_start=payload.slot_start,
                    new_end=payload.slot_end,
                    duration_minutes=duration,
                    location=location,
                    message=reschedule_message,
                )
                outlook_updated = sent_payload.get("status") == "rescheduled"
            except Exception as exc:
                logger.warning(
                    "meeting_registry_reschedule_outlook_failed",
                    ref_key=normalized_ref,
                    error=str(exc),
                )
                sent_payload = None

        if not outlook_updated:
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
                new_invite_sent = True
            except Exception as exc:
                raise MeetingServiceError(
                    f"Не удалось перенести совещание через Outlook/Exchange: {exc}"
                ) from exc

        entry = await registry.apply_reschedule(
            memo_ref_key=normalized_ref,
            slot_start=payload.slot_start,
            slot_end=payload.slot_end,
            subject=subject,
            location=location or None,
            attendees=emails,
            rescheduled_by=current_user,
            sent_payload=sent_payload if isinstance(sent_payload, dict) else None,
            reschedule_message=reschedule_message,
            participant_names=registry_participant_names(entry),
            attendee_details=attendee_details,
            memo_detail=memo_detail,
        )

        history_message = (
            f"Совещание перенесено на {format_slot_label(payload.slot_start, payload.slot_end)}"
        )
        await self._sync_meeting_slot_to_cache(
            normalized_ref,
            slot_start=payload.slot_start,
            slot_end=payload.slot_end,
            location=location or None,
            history_message=history_message,
        )

        await self.audit.log(
            action="meeting.registry_rescheduled",
            actor_id=current_user.id,
            resource_type="meeting_registry",
            resource_id=normalized_ref,
            payload={
                "subject": subject,
                "previous_slot_label": previous_label,
                "new_start": payload.slot_start,
                "new_end": payload.slot_end,
                "outlook_updated": outlook_updated,
                "new_invite_sent": new_invite_sent,
            },
        )

        outlook_item_id = entry.outlook_item_id
        outlook_changekey = entry.outlook_changekey
        outlook_meeting_url = entry.outlook_meeting_url
        if isinstance(sent_payload, dict):
            outlook_item_id = sent_payload.get("outlook_item_id") or outlook_item_id
            outlook_changekey = sent_payload.get("outlook_changekey") or outlook_changekey
            outlook_meeting_url = sent_payload.get("outlook_meeting_url") or outlook_meeting_url

        return MeetingRegistryRescheduleApproveRead(
            ref_key=normalized_ref,
            stage=MeetingRegistryStageRead.INVITATIONS_SENT,
            previous_slot_label=previous_label,
            slot_label=format_slot_label(payload.slot_start, payload.slot_end),
            subject=subject,
            start=payload.slot_start,
            end=payload.slot_end,
            location=location or None,
            attendees=(
                sent_payload.get("attendees") if isinstance(sent_payload, dict) else None
            ) or emails,
            rescheduled=True,
            outlook_updated=outlook_updated,
            new_invite_sent=new_invite_sent,
            message=reschedule_message,
            outlook_item_id=outlook_item_id,
            outlook_changekey=outlook_changekey,
            outlook_meeting_url=outlook_meeting_url,
        )

    async def _sync_meeting_slot_to_cache(
        self,
        memo_ref_key: str,
        *,
        slot_start: str,
        slot_end: str,
        location: str | None,
        history_message: str | None = None,
    ) -> None:
        from app.core.config import settings

        if not settings.MEETING_DASHBOARD_CACHE_ENABLED:
            return
        normalized = memo_ref_key.strip().lower()
        try:
            await MeetingMemoCacheService().patch_meeting_slot(
                normalized,
                slot_start=slot_start,
                slot_end=slot_end,
                location=location,
                history_message=history_message,
            )
            await MeetingDashboardCacheService().patch_meeting_slot(
                normalized,
                slot_start=slot_start,
                slot_end=slot_end,
                location=location,
            )
        except Exception as exc:
            logger.warning(
                "meeting_slot_cache_patch_failed",
                ref_key=normalized,
                error=str(exc),
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
