from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import MeetingRegistryStage
from app.models.meeting_registry import MeetingRegistryEntry
from app.models.user import User
from app.services.meeting_attendees import participants_from_detail
from app.services.meeting_invite_format import (
    format_invite_location_from_detail,
    manager_name_from_detail,
    place_from_detail,
    resolve_invite_subject,
)
from app.services.meeting_slot import parse_slot_datetime

STAGE_ORDER: tuple[MeetingRegistryStage, ...] = (
    MeetingRegistryStage.INVITATIONS_SENT,
    MeetingRegistryStage.PROTOCOL_CREATED,
    MeetingRegistryStage.PROTOCOL_CONDUCTED,
    MeetingRegistryStage.MEETING_COMPLETED,
)


def _person_name(person: dict[str, Any] | None) -> str | None:
    if not isinstance(person, dict):
        return None
    for key in ("full_name", "Description"):
        value = person.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _normalize_participant_names(names: list[str] | None) -> list[str]:
    unique: list[str] = []
    seen: set[str] = set()
    for raw in names or []:
        name = str(raw or "").strip()
        if not name:
            continue
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        unique.append(name)
    return unique


def participant_names_diff(
    current: list[str] | None,
    target: list[str] | None,
) -> tuple[list[str], list[str]]:
    current_norm = _normalize_participant_names(current)
    target_norm = _normalize_participant_names(target)
    current_by_key = {name.casefold(): name for name in current_norm}
    target_by_key = {name.casefold(): name for name in target_norm}
    removed = [current_by_key[key] for key in current_by_key if key not in target_by_key]
    added = [target_by_key[key] for key in target_by_key if key not in current_by_key]
    return added, removed


def resolve_registry_participant_names(
    *,
    memo_detail: dict[str, Any] | None = None,
    participant_names: list[str] | None = None,
    attendee_details: list[Any] | None = None,
) -> list[str]:
    """ФИО для колонки participants: явный список → detail СЗ → attendee_details."""
    explicit = _normalize_participant_names(participant_names)
    if explicit:
        return explicit

    if memo_detail:
        from_memo = _normalize_participant_names(participants_from_detail(memo_detail))
        if from_memo:
            return from_memo

    from_details: list[str] = []
    for item in attendee_details or []:
        if isinstance(item, dict):
            fio = item.get("fio") or item.get("full_name")
        else:
            fio = getattr(item, "fio", None) or getattr(item, "full_name", None)
        if isinstance(fio, str) and fio.strip():
            from_details.append(fio.strip())
    return _normalize_participant_names(from_details)


def _snapshot_from_detail(
    memo_detail: dict[str, Any] | None,
    *,
    subject: str | None,
    location: str | None,
    participant_names: list[str] | None = None,
) -> dict[str, Any]:
    application = (memo_detail or {}).get("application") or {}
    title = (memo_detail or {}).get("title")
    if not isinstance(title, str) or not title.strip():
        title = resolve_invite_subject(memo_detail, override=subject)
    resolved_location = location or format_invite_location_from_detail(memo_detail)
    if not resolved_location:
        resolved_location = place_from_detail(memo_detail)
    participants = _normalize_participant_names(participant_names)
    if not participants and memo_detail:
        participants = _normalize_participant_names(participants_from_detail(memo_detail))
    count = len(participants)
    if count <= 0:
        count = int(application.get("participants_count") or 0)
    return {
        "memo_number": (memo_detail or {}).get("number"),
        "title": title,
        "subject": subject or resolve_invite_subject(memo_detail),
        "location": resolved_location,
        "initiator_name": _person_name(application.get("initiator")),
        "manager_name": manager_name_from_detail(memo_detail)
        or _person_name(application.get("manager")),
        "participants": participants,
        "participants_count": count,
    }


def _outlook_fields_from_sent_payload(sent_payload: dict[str, Any] | None) -> dict[str, str | None]:
    sent = sent_payload or {}
    return {
        "outlook_item_id": sent.get("outlook_item_id") or sent.get("id"),
        "outlook_changekey": sent.get("outlook_changekey") or sent.get("changekey"),
        "outlook_meeting_url": sent.get("outlook_meeting_url") or sent.get("meeting_url"),
    }


def stage_index(stage: MeetingRegistryStage) -> int:
    if stage == MeetingRegistryStage.CANCELLED:
        return -1
    try:
        return STAGE_ORDER.index(stage)
    except ValueError:
        return 0


def build_stage_counts(entries: list[MeetingRegistryEntry]) -> dict[str, int]:
    total = len(entries)
    active_entries = [
        entry for entry in entries if entry.stage != MeetingRegistryStage.CANCELLED
    ]
    counts = {
        "all": total,
        "approved": len(active_entries),
        "cancelled": total - len(active_entries),
        "invitations_sent": 0,
        "protocol_created": 0,
        "protocol_conducted": 0,
        "meeting_completed": 0,
    }
    for entry in active_entries:
        index = stage_index(entry.stage)
        if index >= 0:
            counts["invitations_sent"] += 1
        if index >= 1:
            counts["protocol_created"] += 1
        if index >= 2:
            counts["protocol_conducted"] += 1
        if index >= 3:
            counts["meeting_completed"] += 1
    return counts


class MeetingRegistryService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def upsert_from_invite(
        self,
        *,
        memo_ref_key: str,
        slot_start: str,
        slot_end: str,
        subject: str | None,
        location: str | None,
        attendees: list[str],
        approved_by: User,
        memo_detail: dict[str, Any] | None = None,
        sent_payload: dict[str, Any] | None = None,
        approved_at: datetime | None = None,
        participant_names: list[str] | None = None,
        attendee_details: list[Any] | None = None,
    ) -> MeetingRegistryEntry:
        normalized_ref = memo_ref_key.strip().lower()
        now = datetime.now(timezone.utc)
        names = resolve_registry_participant_names(
            memo_detail=memo_detail,
            participant_names=participant_names,
            attendee_details=attendee_details,
        )
        snapshot = _snapshot_from_detail(
            memo_detail,
            subject=subject,
            location=location,
            participant_names=names,
        )
        participants_count = len(names) if names else int(snapshot.get("participants_count") or 0)
        outlook_fields = _outlook_fields_from_sent_payload(sent_payload)
        slot_start_dt = parse_slot_datetime(slot_start)
        slot_end_dt = parse_slot_datetime(slot_end)

        result = await self.db.execute(
            select(MeetingRegistryEntry).where(MeetingRegistryEntry.memo_ref_key == normalized_ref)
        )
        entry = result.scalar_one_or_none()
        payload = {
            "attendees": attendees,
            "sent_payload": sent_payload or {},
        }

        if entry is None:
            entry = MeetingRegistryEntry(
                memo_ref_key=normalized_ref,
                memo_number=snapshot.get("memo_number"),
                title=snapshot.get("title"),
                subject=snapshot.get("subject"),
                location=snapshot.get("location"),
                initiator_name=snapshot.get("initiator_name"),
                manager_name=snapshot.get("manager_name"),
                participants=names,
                participants_count=participants_count,
                slot_start=slot_start_dt,
                slot_end=slot_end_dt,
                stage=MeetingRegistryStage.INVITATIONS_SENT,
                invitations_sent_at=now,
                approved_at=approved_at,
                approved_by_user_id=approved_by.id,
                outlook_item_id=outlook_fields.get("outlook_item_id"),
                outlook_changekey=outlook_fields.get("outlook_changekey"),
                outlook_meeting_url=outlook_fields.get("outlook_meeting_url"),
                payload=payload,
            )
            self.db.add(entry)
        else:
            if entry.stage == MeetingRegistryStage.CANCELLED:
                await self.db.flush()
                return entry
            if entry.stage == MeetingRegistryStage.INVITATIONS_SENT:
                entry.stage = MeetingRegistryStage.INVITATIONS_SENT
            entry.memo_number = snapshot.get("memo_number") or entry.memo_number
            entry.title = snapshot.get("title") or entry.title
            entry.subject = snapshot.get("subject") or entry.subject
            entry.location = snapshot.get("location") or entry.location
            entry.initiator_name = snapshot.get("initiator_name") or entry.initiator_name
            entry.manager_name = snapshot.get("manager_name") or entry.manager_name
            if names:
                entry.participants = names
                entry.participants_count = len(names)
            elif participants_count:
                entry.participants_count = participants_count
            entry.slot_start = slot_start_dt or entry.slot_start
            entry.slot_end = slot_end_dt or entry.slot_end
            entry.invitations_sent_at = now
            if approved_at is not None:
                entry.approved_at = approved_at
            entry.approved_by_user_id = approved_by.id
            if outlook_fields.get("outlook_item_id"):
                entry.outlook_item_id = outlook_fields["outlook_item_id"]
            if outlook_fields.get("outlook_changekey"):
                entry.outlook_changekey = outlook_fields["outlook_changekey"]
            if outlook_fields.get("outlook_meeting_url"):
                entry.outlook_meeting_url = outlook_fields["outlook_meeting_url"]
            entry.payload = payload

        await self.db.flush()
        return entry

    async def list_entries(
        self,
        *,
        stage_filter: str | None = None,
    ) -> list[MeetingRegistryEntry]:
        query = select(MeetingRegistryEntry).order_by(
            MeetingRegistryEntry.invitations_sent_at.desc(),
            MeetingRegistryEntry.updated_at.desc(),
        )
        normalized_filter = (stage_filter or "all").strip().lower()
        if normalized_filter not in {"", "all", "approved"}:
            try:
                stage = MeetingRegistryStage(normalized_filter)
            except ValueError as exc:
                raise ValueError(f"Unknown registry stage filter: {stage_filter}") from exc
            query = query.where(MeetingRegistryEntry.stage == stage)
        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def get_entry(self, memo_ref_key: str) -> MeetingRegistryEntry | None:
        normalized_ref = memo_ref_key.strip().lower()
        result = await self.db.execute(
            select(MeetingRegistryEntry).where(MeetingRegistryEntry.memo_ref_key == normalized_ref)
        )
        return result.scalar_one_or_none()

    async def mark_cancelled(
        self,
        *,
        memo_ref_key: str,
        cancelled_by: User,
        message: str | None = None,
        cancel_payload: dict[str, Any] | None = None,
        outlook_cancelled: bool = False,
    ) -> MeetingRegistryEntry:
        entry = await self.get_entry(memo_ref_key)
        if entry is None:
            raise ValueError("Совещание не найдено в реестре")
        if entry.stage == MeetingRegistryStage.CANCELLED:
            return entry

        now = datetime.now(timezone.utc)
        entry.stage = MeetingRegistryStage.CANCELLED
        payload = dict(entry.payload or {})
        payload["cancelled_at"] = now.isoformat()
        payload["cancelled_by_user_id"] = str(cancelled_by.id)
        if message:
            payload["cancel_message"] = message
        if cancel_payload:
            payload["cancel_payload"] = cancel_payload
        payload["outlook_cancelled"] = outlook_cancelled
        entry.payload = payload
        await self.db.flush()
        await self.db.refresh(entry)
        return entry

    async def apply_reschedule(
        self,
        *,
        memo_ref_key: str,
        slot_start: str,
        slot_end: str,
        subject: str | None,
        location: str | None,
        attendees: list[str],
        rescheduled_by: User,
        sent_payload: dict[str, Any] | None = None,
        reschedule_message: str | None = None,
        participant_names: list[str] | None = None,
        attendee_details: list[Any] | None = None,
        memo_detail: dict[str, Any] | None = None,
    ) -> MeetingRegistryEntry:
        entry = await self.get_entry(memo_ref_key)
        if entry is None:
            raise ValueError("Совещание не найдено в реестре")

        now = datetime.now(timezone.utc)
        slot_start_dt = parse_slot_datetime(slot_start)
        slot_end_dt = parse_slot_datetime(slot_end)
        outlook_fields = _outlook_fields_from_sent_payload(sent_payload)
        names = resolve_registry_participant_names(
            memo_detail=memo_detail,
            participant_names=participant_names,
            attendee_details=attendee_details,
        )
        payload = dict(entry.payload or {})
        payload["attendees"] = attendees
        payload["sent_payload"] = sent_payload or {}
        payload["rescheduled_at"] = now.isoformat()
        payload["rescheduled_by_user_id"] = str(rescheduled_by.id)
        if reschedule_message:
            payload["reschedule_message"] = reschedule_message
        for key in (
            "cancelled_at",
            "cancelled_by_user_id",
            "cancel_message",
            "cancel_payload",
            "outlook_cancelled",
        ):
            payload.pop(key, None)

        entry.stage = MeetingRegistryStage.INVITATIONS_SENT
        if slot_start_dt is not None:
            entry.slot_start = slot_start_dt
        if slot_end_dt is not None:
            entry.slot_end = slot_end_dt
        if subject:
            entry.subject = subject
        if location:
            entry.location = location
        if names:
            entry.participants = names
            entry.participants_count = len(names)
        entry.invitations_sent_at = now
        entry.approved_by_user_id = rescheduled_by.id
        if outlook_fields.get("outlook_item_id"):
            entry.outlook_item_id = outlook_fields["outlook_item_id"]
        if outlook_fields.get("outlook_changekey"):
            entry.outlook_changekey = outlook_fields["outlook_changekey"]
        if outlook_fields.get("outlook_meeting_url"):
            entry.outlook_meeting_url = outlook_fields["outlook_meeting_url"]
        entry.payload = payload
        await self.db.flush()
        await self.db.refresh(entry)
        return entry

    async def apply_participants_update(
        self,
        memo_ref_key: str,
        *,
        participants: list[str],
        attendees: list[str],
        updated_by: User,
        apply_message: str | None = None,
        outlook_payload: dict[str, Any] | None = None,
    ) -> MeetingRegistryEntry:
        entry = await self.get_entry(memo_ref_key)
        if entry is None:
            raise ValueError("Совещание не найдено в реестре")
        if entry.stage == MeetingRegistryStage.CANCELLED:
            raise ValueError("Нельзя изменить участников отменённого совещания")

        names = _normalize_participant_names(participants)
        now = datetime.now(timezone.utc)
        payload = dict(entry.payload or {})
        payload["attendees"] = attendees
        payload["participants_updated_at"] = now.isoformat()
        payload["participants_updated_by_user_id"] = str(updated_by.id)
        if apply_message:
            payload["participants_update_message"] = apply_message
        if outlook_payload:
            payload["participants_update_payload"] = outlook_payload
            target_id = outlook_payload.get("target_id")
            if isinstance(target_id, str) and target_id.strip():
                entry.outlook_item_id = target_id.strip()

        entry.participants = names
        entry.participants_count = len(names)
        entry.payload = payload
        await self.db.flush()
        await self.db.refresh(entry)
        return entry

    async def sync_protocol_stages(self) -> None:
        """Заготовка для синхронизации этапов протокола из 1С."""
        return None
