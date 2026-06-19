from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.meeting_agent.backend import (
    InviteDraft,
    MeetingBackend,
    MeetingBackendError,
    MeetingMemo,
    MeetingRoomOption,
    MeetingSlot,
    ResolvedParticipant,
    _duration_from_memo,
    _normalize_memo,
)
from app.agents.meeting_agent.config import AGENT_NAME
from app.models.agent import Agent
from app.models.enums import TaskStatus
from app.models.task import Task
from app.models.user import User
from app.schemas.meeting import (
    MeetingAgentSlotApproveRead,
    MeetingAgentSlotApproveRequest,
    MeetingAgentSlotPreviewRead,
    MeetingAgentSlotPreviewRequest,
    MeetingAttendeeRead,
    MeetingInviteDraftRead,
    MeetingInvitePreviewRequest,
    MeetingInviteSendRequest,
    MeetingMemoRead,
    MeetingRoomRead,
    MeetingRoomsRequest,
    MeetingRunCreate,
    MeetingRunRead,
    MeetingRunResultRead,
    MeetingSlotRead,
    MeetingSlotsRequest,
)
from app.schemas.task import TaskResultCreate
from app.services.audit_service import AuditService
from app.services.meeting_agent_approve import (
    ATTENDEE_ROLE_LABELS,
    MeetingApproveError,
    build_approve_invite_body,
    resolve_approve_recipients,
)
from app.services.meeting_attendees import collect_attendees_from_detail, emails_by_fio_from_detail
from app.services.meeting_memo_cache import (
    MeetingMemoCacheService,
    MemoCacheMissError,
    detail_to_memo_document,
)
from app.services.meeting_permission import MEETING_AGENT_SLUG, can_access_meeting_agent
from app.services.meeting_slot import format_planned_start_for_search, format_slot_label, slot_duration_minutes
from app.services.permission_service import PermissionService
from app.services.task_service import TaskService
from app.tools.Outlook.send_meeting_invite import dispatch_meeting_invite


class MeetingServiceError(ValueError):
    pass


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
        return _memo_read(memo)

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
        return [_slot_read(item) for item in slots]

    async def _resolve_memo_attendees(
        self,
        detail: dict,
        *,
        backend: MeetingBackend,
        current_user: User,
    ) -> tuple[MeetingMemo, list[ResolvedParticipant], list[MeetingAttendeeRead], list[str]]:
        attendee_specs = collect_attendees_from_detail(detail)
        if not attendee_specs:
            raise MeetingServiceError(
                "В заявке нет участников, инициатора или руководителя для отправки приглашений"
            )

        memo = _normalize_memo(detail_to_memo_document(detail))
        cached_emails = emails_by_fio_from_detail(detail)
        need_lookup = [fio for fio, _role in attendee_specs if fio not in cached_emails]
        resolved_lookup = (
            await backend.resolve_participants(need_lookup, current_user=current_user)
            if need_lookup
            else []
        )
        resolved_by_fio = {item.fio: item for item in resolved_lookup}

        attendees: list[MeetingAttendeeRead] = []
        missing_emails: list[str] = []
        resolved: list[ResolvedParticipant] = []
        for fio, role in attendee_specs:
            cached_email = cached_emails.get(fio)
            match = resolved_by_fio.get(fio)
            email = cached_email or (match.email if match else None)
            found = bool(email)
            if not found:
                missing_emails.append(fio)
            else:
                resolved.append(ResolvedParticipant(fio=fio, email=email, found=True))
            attendees.append(
                MeetingAttendeeRead(
                    fio=fio,
                    email=email,
                    role=role,
                    role_label=ATTENDEE_ROLE_LABELS.get(role, role),
                    found=found,
                )
            )
        return memo, resolved, attendees, missing_emails

    async def suggest_agent_slot(
        self,
        memo_ref_key: str,
        payload: MeetingAgentSlotPreviewRequest,
        *,
        current_user: User,
    ) -> MeetingAgentSlotPreviewRead:
        """Ближайший слот для модалки «Запустить агента»: участники + инициатор + руководитель."""
        await self._ensure_access(current_user)
        normalized_ref = memo_ref_key.strip().lower()
        try:
            detail, _fetched_at, _from_cache = await MeetingMemoCacheService().get_memo_detail(normalized_ref)
        except MemoCacheMissError as exc:
            raise MeetingServiceError(str(exc)) from exc

        backend = self._backend()
        application = detail.get("application") or {}
        try:
            memo, resolved, attendees, missing_emails = await self._resolve_memo_attendees(
                detail,
                backend=backend,
                current_user=current_user,
            )
        except MeetingBackendError as exc:
            duration = (
                payload.duration_minutes
                or application.get("duration_minutes")
                or 60
            )
            return MeetingAgentSlotPreviewRead(
                memo_ref_key=normalized_ref,
                duration_minutes=duration,
                attendees=[],
                missing_emails=[],
                error=str(exc),
            )

        duration = payload.duration_minutes or application.get("duration_minutes") or _duration_from_memo(memo)
        planned_start = format_planned_start_for_search(
            application.get("meeting_start"),
            detail.get("queue") or {},
        )

        if missing_emails:
            return MeetingAgentSlotPreviewRead(
                memo_ref_key=normalized_ref,
                duration_minutes=duration,
                attendees=attendees,
                missing_emails=missing_emails,
                error="Не найден корпоративный e-mail: " + ", ".join(missing_emails),
            )

        try:
            slots = await backend.find_slots(
                memo=memo,
                participants=resolved,
                planned_start=planned_start,
                duration_minutes=duration,
                current_user=current_user,
            )
        except MeetingBackendError as exc:
            return MeetingAgentSlotPreviewRead(
                memo_ref_key=normalized_ref,
                duration_minutes=duration,
                attendees=attendees,
                missing_emails=missing_emails,
                error=str(exc),
            )

        if not slots:
            return MeetingAgentSlotPreviewRead(
                memo_ref_key=normalized_ref,
                duration_minutes=duration,
                attendees=attendees,
                missing_emails=missing_emails,
                error="Свободный слот не найден в заданном окне поиска",
            )

        slot = _slot_read(slots[0])
        return MeetingAgentSlotPreviewRead(
            memo_ref_key=normalized_ref,
            slot=slot,
            slot_label=format_slot_label(slot.start, slot.end),
            duration_minutes=duration,
            attendees=attendees,
            missing_emails=missing_emails,
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

        try:
            attendee_details, resolved = resolve_approve_recipients(payload)
        except MeetingApproveError as exc:
            raise MeetingServiceError(str(exc)) from exc

        emails = [item.email for item in resolved if item.email]
        if not emails:
            raise MeetingServiceError("Не указаны e-mail участников для отправки приглашения")

        subject = (payload.subject or "").strip() or "Совещание"
        location = (payload.location or "").strip()
        duration = slot_duration_minutes(payload.slot_start, payload.slot_end)
        body = build_approve_invite_body(subject)

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
                resources=[],
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
        return [_room_read(item) for item in rooms]

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
        return _invite_read(draft)

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
        return result

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


def _memo_read(memo: MeetingMemo) -> MeetingMemoRead:
    return MeetingMemoRead(
        ref_key=memo.ref_key,
        number=memo.number,
        date=memo.date,
        subject=memo.subject,
        meeting_type=memo.meeting_type,
        participant_fio=memo.participant_fio,
    )


def _slot_read(item: MeetingSlot) -> MeetingSlotRead:
    return MeetingSlotRead(start=item.start, end=item.end, confidence=item.confidence)


def _room_read(item: MeetingRoomOption) -> MeetingRoomRead:
    return MeetingRoomRead(name=item.name, email=item.email, available=item.available)


def _invite_read(item: InviteDraft) -> MeetingInviteDraftRead:
    return MeetingInviteDraftRead(
        subject=item.subject,
        start=item.start,
        end=item.end,
        location=item.location,
        attendees=item.attendees,
        body=item.body,
    )
