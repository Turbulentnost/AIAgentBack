from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.models.enums import MeetingRegistryEventType, MeetingRegistryStage
from app.models.meeting_registry import MeetingRegistryEntry
from app.services.meeting_registry_service import MeetingRegistryService
from app.tools.onec.create_protocol import create_meeting_protocol, delete_meeting_protocol

logger = get_logger(__name__)

SCHEDULABLE_STAGES = {
    MeetingRegistryStage.SCHEDULED,
    MeetingRegistryStage.INVITATIONS_SENT,
}


def compute_protocol_draft_at(slot_start: datetime, *, minutes_before: int) -> datetime:
    return slot_start - timedelta(minutes=minutes_before)


def read_topic_department_key(topic: dict[str, Any] | None) -> str | None:
    if not topic:
        return None
    keys = topic.get("keys")
    if isinstance(keys, dict):
        raw = keys.get("department")
        if raw:
            return str(raw).strip() or None
    raw = topic.get("department_key")
    if raw:
        return str(raw).strip() or None
    return None


async def resolve_topic_department_key(
    topic_key: str,
    topic: dict[str, Any] | None,
) -> str | None:
    department_key = read_topic_department_key(topic)
    if department_key:
        return department_key

    def _fetch() -> str | None:
        from app.tools.onec.connection import CONFIG, create_session
        from app.tools.onec.meeting_topics_registry import fetch_topic_by_key, normalize_topic

        session = create_session(CONFIG)
        row = fetch_topic_by_key(session, CONFIG, topic_key, expand_related=False)
        if not row:
            return None
        normalized = normalize_topic(row, expand_related=False)
        return read_topic_department_key(normalized)

    return await asyncio.to_thread(_fetch)


def read_meeting_topic(entry: MeetingRegistryEntry) -> dict[str, Any] | None:
    payload = entry.payload if isinstance(entry.payload, dict) else {}
    topic = payload.get("meeting_topic")
    return topic if isinstance(topic, dict) else None


def build_protocol_number_stub(entry: MeetingRegistryEntry) -> str:
    template = settings.MEETING_PROTOCOL_DRAFT_NUMBER_TEMPLATE
    memo_number = (entry.memo_number or entry.memo_ref_key[:8] or "memo").replace("/", "_")
    date_part = entry.slot_start.strftime("%Y%m%d") if entry.slot_start else "nodate"
    return template.format(
        memo_number=memo_number,
        date=date_part,
        memo_ref_key=entry.memo_ref_key,
    )


def revoke_protocol_draft_celery_task(celery_task_id: str | None) -> None:
    if not celery_task_id:
        return
    try:
        from app.workers.celery_app import celery_app

        celery_app.control.revoke(celery_task_id, terminate=True, signal="SIGTERM")
        logger.info("meeting.protocol_draft.revoked", celery_task_id=celery_task_id)
    except Exception as exc:
        logger.warning(
            "meeting.protocol_draft.revoke_failed",
            celery_task_id=celery_task_id,
            error=str(exc),
        )


def enqueue_protocol_draft_task(entry_id: uuid.UUID, *, eta: datetime) -> str:
    from app.workers.tasks import create_registry_protocol_draft

    eta_utc = eta.astimezone(timezone.utc)
    result = create_registry_protocol_draft.apply_async(
        args=[str(entry_id)],
        eta=eta_utc,
        queue="default",
    )
    return result.id


class MeetingProtocolDraftService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.registry = MeetingRegistryService(db)

    async def get_entry(self, entry_id: uuid.UUID) -> MeetingRegistryEntry | None:
        result = await self.db.execute(
            select(MeetingRegistryEntry).where(MeetingRegistryEntry.id == entry_id)
        )
        return result.scalar_one_or_none()

    async def get_entry_by_memo_ref(self, memo_ref_key: str) -> MeetingRegistryEntry | None:
        return await self.registry.get_entry(memo_ref_key)

    def _can_schedule(self, entry: MeetingRegistryEntry, *, now: datetime) -> bool:
        if not settings.MEETING_PROTOCOL_DRAFT_ENABLED:
            return False
        if entry.stage == MeetingRegistryStage.CANCELLED:
            return False
        if entry.stage not in SCHEDULABLE_STAGES:
            return False
        if entry.protocol_ref_key:
            return False
        if entry.slot_start is None or entry.protocol_draft_at is None:
            return False
        return entry.protocol_draft_at > now

    async def update_protocol_draft_at(self, entry: MeetingRegistryEntry) -> MeetingRegistryEntry:
        if entry.slot_start is None:
            entry.protocol_draft_at = None
            return entry
        entry.protocol_draft_at = compute_protocol_draft_at(
            entry.slot_start,
            minutes_before=settings.MEETING_PROTOCOL_DRAFT_MINUTES_BEFORE,
        )
        return entry

    async def clear_schedule_fields(self, entry: MeetingRegistryEntry) -> None:
        entry.protocol_draft_celery_task_id = None
        entry.protocol_draft_enqueued_at = None

    async def cancel_protocol_draft_schedule(
        self,
        entry: MeetingRegistryEntry,
        *,
        clear_draft_at: bool = False,
    ) -> MeetingRegistryEntry:
        revoke_protocol_draft_celery_task(entry.protocol_draft_celery_task_id)
        await self.clear_schedule_fields(entry)
        if clear_draft_at:
            entry.protocol_draft_at = None
        await self.db.flush()
        return entry

    async def schedule_protocol_draft(
        self,
        entry: MeetingRegistryEntry,
        *,
        force: bool = False,
    ) -> MeetingRegistryEntry:
        now = datetime.now(timezone.utc)
        await self.update_protocol_draft_at(entry)

        if not force and entry.protocol_draft_celery_task_id:
            return entry
        if not self._can_schedule(entry, now=now):
            await self.cancel_protocol_draft_schedule(entry)
            await self.db.flush()
            return entry

        if entry.protocol_draft_celery_task_id:
            revoke_protocol_draft_celery_task(entry.protocol_draft_celery_task_id)
            await self.clear_schedule_fields(entry)

        task_id = enqueue_protocol_draft_task(entry.id, eta=entry.protocol_draft_at)
        entry.protocol_draft_celery_task_id = task_id
        entry.protocol_draft_enqueued_at = now
        entry.protocol_draft_error = None

        await self.registry.append_event(
            entry,
            event_type=MeetingRegistryEventType.PROTOCOL_DRAFT_SCHEDULED,
            message=(
                f"Запланировано создание черновика протокола на "
                f"{entry.protocol_draft_at.astimezone(timezone.utc).isoformat()}"
            ),
            payload={
                "protocol_draft_at": entry.protocol_draft_at.isoformat(),
                "celery_task_id": task_id,
            },
        )
        await self.db.flush()
        logger.info(
            "meeting.protocol_draft.scheduled",
            entry_id=str(entry.id),
            memo_ref_key=entry.memo_ref_key,
            protocol_draft_at=entry.protocol_draft_at.isoformat(),
            celery_task_id=task_id,
        )
        return entry

    async def refresh_protocol_draft_schedule(
        self,
        entry: MeetingRegistryEntry,
    ) -> MeetingRegistryEntry:
        await self.update_protocol_draft_at(entry)
        return await self.schedule_protocol_draft(entry, force=True)

    async def recreate_protocol_draft_on_reschedule(
        self,
        entry: MeetingRegistryEntry,
    ) -> MeetingRegistryEntry:
        if entry.protocol_ref_key:
            await self._delete_existing_protocol(entry)
            entry.protocol_ref_key = None
            entry.protocol_number = None
            entry.protocol_draft_created_at = None
            if entry.stage == MeetingRegistryStage.PROTOCOL_CREATED:
                entry.stage = MeetingRegistryStage.INVITATIONS_SENT

        await self.cancel_protocol_draft_schedule(entry)
        return await self.refresh_protocol_draft_schedule(entry)

    async def save_meeting_topic(
        self,
        entry: MeetingRegistryEntry,
        *,
        topic: dict[str, Any],
    ) -> MeetingRegistryEntry:
        payload = dict(entry.payload or {})
        payload["meeting_topic"] = {
            **topic,
            "resolved_at": datetime.now(timezone.utc).isoformat(),
        }
        entry.payload = payload
        await self.db.flush()
        return entry

    async def _delete_existing_protocol(self, entry: MeetingRegistryEntry) -> None:
        ref_key = (entry.protocol_ref_key or "").strip()
        number = (entry.protocol_number or "").strip() or None
        if not ref_key and not number:
            return

        def _delete() -> dict[str, Any]:
            return delete_meeting_protocol(ref_key=ref_key or None, number=number)

        try:
            await asyncio.to_thread(_delete)
        except Exception as exc:
            logger.warning(
                "meeting.protocol_draft.delete_failed",
                entry_id=str(entry.id),
                protocol_ref_key=ref_key,
                error=str(exc),
            )
            raise

    def _should_create_now(self, entry: MeetingRegistryEntry, *, now: datetime) -> bool:
        if entry.stage == MeetingRegistryStage.CANCELLED:
            return False
        if entry.protocol_ref_key:
            return False
        if entry.slot_start is None:
            return False
        if entry.protocol_draft_at is None:
            return False
        grace = timedelta(minutes=2)
        return entry.protocol_draft_at <= now + grace

    async def create_protocol_draft_for_entry(self, entry_id: uuid.UUID) -> dict[str, Any]:
        entry = await self.get_entry(entry_id)
        if entry is None:
            raise ValueError(f"Запись реестра не найдена: {entry_id}")

        now = datetime.now(timezone.utc)
        if not self._should_create_now(entry, now=now):
            return {
                "skipped": True,
                "reason": "not_due_or_not_eligible",
                "entry_id": str(entry_id),
            }

        topic = read_meeting_topic(entry)
        topic_key = (topic or {}).get("ref_key")
        if not topic_key:
            message = "Тема совещания не сохранена в реестре — черновик протокола не создан"
            entry.protocol_draft_error = message
            await self.db.flush()
            logger.warning(
                "meeting.protocol_draft.skipped_no_topic",
                entry_id=str(entry.id),
                memo_ref_key=entry.memo_ref_key,
            )
            return {
                "skipped": True,
                "reason": "missing_meeting_topic",
                "entry_id": str(entry_id),
                "message": message,
            }

        meeting_type = (topic or {}).get("meeting_type")
        comment = (entry.subject or entry.title or "").strip()
        department_key = await resolve_topic_department_key(str(topic_key), topic)

        def _create() -> dict[str, Any]:
            return create_meeting_protocol(
                comment=comment,
                template_number_prefix=settings.MEETING_PROTOCOL_DRAFT_TEMPLATE_PREFIX,
                manager_fio=entry.manager_name,
                topic_key=str(topic_key),
                meeting_type=str(meeting_type) if meeting_type else None,
                department_key=department_key,
            )

        try:
            raw = await asyncio.to_thread(_create)
        except Exception as exc:
            entry.protocol_draft_error = str(exc)
            await self.db.flush()
            logger.exception(
                "meeting.protocol_draft.failed",
                entry_id=str(entry.id),
                memo_ref_key=entry.memo_ref_key,
            )
            raise

        protocol = raw.get("protocol") or {}
        entry.protocol_ref_key = protocol.get("ref_key")
        entry.protocol_number = protocol.get("number")
        entry.protocol_draft_created_at = now
        entry.protocol_draft_error = None
        entry.stage = MeetingRegistryStage.PROTOCOL_CREATED
        await self.clear_schedule_fields(entry)

        await self.registry.append_event(
            entry,
            event_type=MeetingRegistryEventType.STAGE_CHANGED,
            message=(
                f"Создан черновик протокола №{entry.protocol_number or '?'} "
                f"({entry.protocol_draft_created_at.isoformat()})"
            ),
            payload={
                "protocol_ref_key": entry.protocol_ref_key,
                "protocol_number": entry.protocol_number,
            },
        )
        await self.db.flush()
        logger.info(
            "meeting.protocol_draft.created",
            entry_id=str(entry.id),
            memo_ref_key=entry.memo_ref_key,
            protocol_number=entry.protocol_number,
        )
        return {
            "created": True,
            "entry_id": str(entry.id),
            "protocol_ref_key": entry.protocol_ref_key,
            "protocol_number": entry.protocol_number,
        }
