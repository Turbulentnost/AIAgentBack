from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import MeetingRegistryStage
from app.models.meeting_registry import MeetingRegistryEntry
from app.models.user import User
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


def _snapshot_from_detail(
    memo_detail: dict[str, Any] | None,
    *,
    subject: str | None,
    location: str | None,
) -> dict[str, Any]:
    application = (memo_detail or {}).get("application") or {}
    title = (memo_detail or {}).get("title")
    if not isinstance(title, str) or not title.strip():
        title = resolve_invite_subject(memo_detail, override=subject)
    resolved_location = location or format_invite_location_from_detail(memo_detail)
    if not resolved_location:
        resolved_location = place_from_detail(memo_detail)
    return {
        "memo_number": (memo_detail or {}).get("number"),
        "title": title,
        "subject": subject or resolve_invite_subject(memo_detail),
        "location": resolved_location,
        "initiator_name": _person_name(application.get("initiator")),
        "manager_name": manager_name_from_detail(memo_detail)
        or _person_name(application.get("manager")),
        "participants_count": int(application.get("participants_count") or 0),
    }


def stage_index(stage: MeetingRegistryStage) -> int:
    try:
        return STAGE_ORDER.index(stage)
    except ValueError:
        return 0


def build_stage_counts(entries: list[MeetingRegistryEntry]) -> dict[str, int]:
    total = len(entries)
    counts = {
        "all": total,
        "approved": total,
        "invitations_sent": 0,
        "protocol_created": 0,
        "protocol_conducted": 0,
        "meeting_completed": 0,
    }
    for entry in entries:
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
    ) -> MeetingRegistryEntry:
        normalized_ref = memo_ref_key.strip().lower()
        now = datetime.now(timezone.utc)
        snapshot = _snapshot_from_detail(memo_detail, subject=subject, location=location)
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
                participants_count=snapshot.get("participants_count") or 0,
                slot_start=slot_start_dt,
                slot_end=slot_end_dt,
                stage=MeetingRegistryStage.INVITATIONS_SENT,
                invitations_sent_at=now,
                approved_at=approved_at,
                approved_by_user_id=approved_by.id,
                payload=payload,
            )
            self.db.add(entry)
        else:
            if entry.stage == MeetingRegistryStage.INVITATIONS_SENT:
                entry.stage = MeetingRegistryStage.INVITATIONS_SENT
            entry.memo_number = snapshot.get("memo_number") or entry.memo_number
            entry.title = snapshot.get("title") or entry.title
            entry.subject = snapshot.get("subject") or entry.subject
            entry.location = snapshot.get("location") or entry.location
            entry.initiator_name = snapshot.get("initiator_name") or entry.initiator_name
            entry.manager_name = snapshot.get("manager_name") or entry.manager_name
            entry.participants_count = snapshot.get("participants_count") or entry.participants_count
            entry.slot_start = slot_start_dt or entry.slot_start
            entry.slot_end = slot_end_dt or entry.slot_end
            entry.invitations_sent_at = now
            if approved_at is not None:
                entry.approved_at = approved_at
            entry.approved_by_user_id = approved_by.id
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

    async def sync_protocol_stages(self) -> None:
        """Заготовка для синхронизации этапов протокола из 1С."""
        return None
