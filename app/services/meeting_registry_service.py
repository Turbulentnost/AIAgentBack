from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import MeetingRegistryEventType, MeetingRegistryStage
from app.models.meeting_registry import MeetingRegistryEntry, MeetingRegistryEvent
from app.models.user import User
from app.services.meeting_attendees import participants_from_detail, registry_participant_names
from app.services.meeting_invite_format import (
    format_invite_location_from_detail,
    manager_name_from_detail,
    place_from_detail,
    resolve_invite_subject,
)
from app.services.meeting_slot import format_slot_label, parse_slot_datetime

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
    registry_entry: Any | None = None,
    memo_detail: dict[str, Any] | None = None,
    participant_names: list[str] | None = None,
    attendee_details: list[Any] | None = None,
) -> list[str]:
    """ФИО участников: для существующей записи реестра — только participants из БД."""
    if registry_entry is not None:
        from_db = registry_participant_names(registry_entry)
        if from_db:
            return from_db

    explicit = _normalize_participant_names(participant_names)
    if explicit:
        return explicit

    from_details: list[str] = []
    for item in attendee_details or []:
        if isinstance(item, dict):
            fio = item.get("fio") or item.get("full_name")
        else:
            fio = getattr(item, "fio", None) or getattr(item, "full_name", None)
        if isinstance(fio, str) and fio.strip():
            from_details.append(fio.strip())
    normalized_details = _normalize_participant_names(from_details)
    if normalized_details:
        return normalized_details

    if memo_detail:
        from_memo = _normalize_participant_names(participants_from_detail(memo_detail))
        if from_memo:
            return from_memo

    return []


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


def _operational_payload(
    *,
    attendees: list[str],
    sent_payload: dict[str, Any] | None = None,
    pending_removal: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "attendees": attendees,
        "sent_payload": sent_payload or {},
    }
    if pending_removal:
        payload["pending_removal"] = pending_removal
    return payload


def _pending_removal_from_entry(entry: MeetingRegistryEntry) -> dict[str, Any] | None:
    payload = entry.payload if isinstance(entry.payload, dict) else {}
    pending = payload.get("pending_removal")
    return pending if isinstance(pending, dict) else None


def _slot_label_from_entry(entry: MeetingRegistryEntry) -> str | None:
    if entry.slot_start is None:
        return None
    start = entry.slot_start.isoformat()
    end = entry.slot_end.isoformat() if entry.slot_end else start
    return format_slot_label(start, end)


class MeetingRegistryService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def append_event(
        self,
        entry: MeetingRegistryEntry,
        *,
        event_type: MeetingRegistryEventType,
        message: str,
        actor: User | None = None,
        occurred_at: datetime | None = None,
        payload: dict[str, Any] | None = None,
    ) -> MeetingRegistryEvent:
        event = MeetingRegistryEvent(
            registry_entry_id=entry.id,
            memo_ref_key=entry.memo_ref_key,
            occurred_at=occurred_at or datetime.now(timezone.utc),
            event_type=event_type,
            message=message.strip(),
            actor_user_id=actor.id if actor else None,
            payload=payload or {},
        )
        self.db.add(event)
        return event

    async def list_events(self, memo_ref_key: str) -> list[MeetingRegistryEvent]:
        normalized_ref = memo_ref_key.strip().lower()
        result = await self.db.execute(
            select(MeetingRegistryEvent)
            .where(MeetingRegistryEvent.memo_ref_key == normalized_ref)
            .order_by(MeetingRegistryEvent.occurred_at.asc(), MeetingRegistryEvent.id.asc())
        )
        return list(result.scalars().all())

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

        result = await self.db.execute(
            select(MeetingRegistryEntry).where(MeetingRegistryEntry.memo_ref_key == normalized_ref)
        )
        entry = result.scalar_one_or_none()

        names = resolve_registry_participant_names(
            registry_entry=entry,
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

        is_new_entry = entry is None
        previous_slot_label = _slot_label_from_entry(entry) if entry else None
        payload = _operational_payload(attendees=attendees, sent_payload=sent_payload)

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
        slot_label = format_slot_label(slot_start, slot_end)
        if is_new_entry:
            invite_message = f"Отправлены приглашения на {slot_label}"
        else:
            invite_message = f"Повторно отправлены приглашения на {slot_label}"
        await self.append_event(
            entry,
            event_type=MeetingRegistryEventType.INVITATIONS_SENT,
            message=invite_message,
            actor=approved_by,
            occurred_at=now,
            payload={
                "slot_start": slot_start,
                "slot_end": slot_end,
                "attendees": attendees,
                "participants_count": participants_count,
                "is_repeat": not is_new_entry,
                "previous_slot_label": previous_slot_label,
            },
        )
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
        entry.cancelled_at = now
        payload = _operational_payload(
            attendees=list((entry.payload or {}).get("attendees") or []),
            sent_payload=(entry.payload or {}).get("sent_payload"),
        )
        entry.payload = payload
        cancel_message = (message or "").strip() or "Совещание отменено"
        await self.append_event(
            entry,
            event_type=MeetingRegistryEventType.CANCELLED,
            message=cancel_message,
            actor=cancelled_by,
            occurred_at=now,
            payload={
                "outlook_cancelled": outlook_cancelled,
                "cancel_payload": cancel_payload or {},
            },
        )
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
        previous_slot_label = _slot_label_from_entry(entry)
        slot_start_dt = parse_slot_datetime(slot_start)
        slot_end_dt = parse_slot_datetime(slot_end)
        outlook_fields = _outlook_fields_from_sent_payload(sent_payload)
        names = resolve_registry_participant_names(
            registry_entry=entry,
            memo_detail=memo_detail,
            participant_names=participant_names,
            attendee_details=attendee_details,
        )
        payload = _operational_payload(attendees=attendees, sent_payload=sent_payload)

        entry.stage = MeetingRegistryStage.INVITATIONS_SENT
        entry.cancelled_at = None
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
        new_slot_label = format_slot_label(slot_start, slot_end)
        reschedule_text = (reschedule_message or "").strip() or (
            f"Совещание перенесено на {new_slot_label}"
        )
        await self.append_event(
            entry,
            event_type=MeetingRegistryEventType.RESCHEDULED,
            message=reschedule_text,
            actor=rescheduled_by,
            occurred_at=now,
            payload={
                "previous_slot_label": previous_slot_label,
                "slot_start": slot_start,
                "slot_end": slot_end,
                "location": location,
                "subject": subject,
            },
        )
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
        previous_names = _normalize_participant_names(
            entry.participants if isinstance(entry.participants, list) else []
        )
        added, removed = participant_names_diff(previous_names, names)
        now = datetime.now(timezone.utc)
        payload = _operational_payload(
            attendees=attendees,
            sent_payload=(entry.payload or {}).get("sent_payload"),
        )
        if outlook_payload:
            target_id = outlook_payload.get("target_id")
            if isinstance(target_id, str) and target_id.strip():
                entry.outlook_item_id = target_id.strip()

        entry.participants = names
        entry.participants_count = len(names)
        entry.payload = payload
        apply_text = (apply_message or "").strip() or "Состав участников совещания изменён"
        await self.append_event(
            entry,
            event_type=MeetingRegistryEventType.PARTICIPANTS_UPDATED,
            message=apply_text,
            actor=updated_by,
            occurred_at=now,
            payload={
                "added": added,
                "removed": removed,
                "participants_count": len(names),
                "outlook_payload": outlook_payload or {},
            },
        )
        await self.db.flush()
        await self.db.refresh(entry)
        return entry

    async def save_pending_removal(
        self,
        memo_ref_key: str,
        *,
        participants: list[str],
        attendees: list[str],
        removed: list[str],
    ) -> MeetingRegistryEntry:
        entry = await self.get_entry(memo_ref_key)
        if entry is None:
            raise ValueError("Совещание не найдено в реестре")

        current_payload = entry.payload if isinstance(entry.payload, dict) else {}
        payload = _operational_payload(
            attendees=attendees,
            sent_payload=current_payload.get("sent_payload"),
            pending_removal={
                "participants": _normalize_participant_names(participants),
                "attendees": attendees,
                "removed": _normalize_participant_names(removed),
            },
        )
        entry.payload = payload
        await self.db.flush()
        await self.db.refresh(entry)
        return entry

    async def clear_pending_removal(self, memo_ref_key: str) -> MeetingRegistryEntry | None:
        entry = await self.get_entry(memo_ref_key)
        if entry is None:
            return None
        current_payload = dict(entry.payload or {})
        current_payload.pop("pending_removal", None)
        entry.payload = current_payload
        await self.db.flush()
        await self.db.refresh(entry)
        return entry

    async def sync_protocol_stages(self) -> None:
        """Заготовка для синхронизации этапов протокола из 1С."""
        return None
