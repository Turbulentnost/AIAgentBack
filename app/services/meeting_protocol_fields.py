from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.meeting_registry import MeetingRegistryEntry
from app.models.scheduled_meeting import ScheduledMeeting

MEMO_DOCUMENT = "Document_ТД_СлужебнаяЗаписка"
MEMO_BASIS_TYPE = f"StandardODATA.{MEMO_DOCUMENT}"
PROTOCOL_BASIS_TYPE = "StandardODATA.Document_ТД_Протокол"
ROOM_TYPE = "StandardODATA.Catalog_CRM_Помещения"
DEPARTMENT_TYPE = "StandardODATA.Catalog_ПодразделенияОрганизаций"
TOPIC_TYPE = "StandardODATA.Catalog_ТД_ТемыСовещаний"


def is_series_entry(entry: MeetingRegistryEntry) -> bool:
    return entry.scheduled_meeting_id is not None


def read_topic_room_key(topic: dict[str, Any] | None) -> str | None:
    if not topic:
        return None
    keys = topic.get("keys")
    if isinstance(keys, dict):
        raw = keys.get("room")
        if raw:
            return str(raw).strip() or None
    raw = topic.get("room_key")
    if raw:
        return str(raw).strip() or None
    return None


def fetch_memo_location_key(
    session,
    config,
    memo_ref_key: str,
) -> str | None:
    from app.tools.onec.lookup_user_ref import is_empty_key

    normalized_ref = (memo_ref_key or "").strip()
    if is_empty_key(normalized_ref):
        return None

    from app.tools.onec.get_meetings import entity_url

    url = f"{entity_url(config.url, MEMO_DOCUMENT)}(guid'{normalized_ref}')?$format=json"
    response = session.get(url, timeout=config.timeout)
    if not response.ok:
        return None
    raw = response.json().get("МестоПроведенияСовещания")
    normalized = str(raw or "").strip()
    return None if is_empty_key(normalized) else normalized


def resolve_room_key(
    entry: MeetingRegistryEntry,
    topic: dict[str, Any] | None,
    *,
    session,
    config,
) -> str | None:
    if not is_series_entry(entry):
        memo_room = fetch_memo_location_key(session, config, entry.memo_ref_key)
        if memo_room:
            return memo_room
    return read_topic_room_key(topic)


def resolve_manager_department_key(
    session,
    manager_fio: str | None,
    *,
    config,
) -> str | None:
    from app.tools.onec.lookup_person_department import resolve_department_key_for_manager_fio

    normalized_fio = (manager_fio or "").strip()
    if not normalized_fio:
        return None
    return resolve_department_key_for_manager_fio(session, normalized_fio, config=config)


def resolve_next_meeting_date_for_series(
    meeting: ScheduledMeeting,
    *,
    current_occurrence_date,
) -> str | None:
    from app.services.scheduled_meeting_occurrences import find_next_after, resolve_series_occurrences

    if current_occurrence_date is None:
        return None

    occurrences, _ = resolve_series_occurrences(
        meeting,
        range_start=meeting.series_start_date,
        range_end=meeting.series_end_date,
    )
    next_occurrence = find_next_after(occurrences, after_date=current_occurrence_date)
    if next_occurrence is None:
        return None
    return f"{next_occurrence.occurrence_date.isoformat()}T00:00:00"


async def resolve_next_meeting_date(
    entry: MeetingRegistryEntry,
    db: AsyncSession,
) -> str | None:
    if not is_series_entry(entry):
        return None
    if entry.series_occurrence_date is None:
        return None

    meeting = await db.get(ScheduledMeeting, entry.scheduled_meeting_id)
    if meeting is None:
        return None

    return await asyncio.to_thread(
        resolve_next_meeting_date_for_series,
        meeting,
        current_occurrence_date=entry.series_occurrence_date,
    )


def resolve_basis_for_protocol(
    entry: MeetingRegistryEntry,
    *,
    topic_key: str,
    session,
    config,
    before: datetime | None = None,
) -> tuple[str | None, str | None]:
    from app.tools.onec.create_protocol import fetch_previous_protocol_by_topic

    if is_series_entry(entry):
        previous = fetch_previous_protocol_by_topic(
            session,
            config,
            topic_key,
            before=before or entry.slot_start,
        )
        if previous is None:
            return None, None
        ref_key = str(previous.get("Ref_Key") or "").strip()
        if not ref_key:
            return None, None
        return ref_key, PROTOCOL_BASIS_TYPE

    memo_ref = (entry.memo_ref_key or "").strip()
    if not memo_ref:
        return None, None
    return memo_ref, MEMO_BASIS_TYPE


async def build_protocol_creation_fields(
    entry: MeetingRegistryEntry,
    topic: dict[str, Any],
    *,
    db: AsyncSession,
) -> dict[str, Any]:
    def _load_sync_fields() -> dict[str, Any]:
        from app.tools.onec.connection import CONFIG, create_session

        session = create_session(CONFIG)
        topic_key = str(topic.get("ref_key") or "").strip()
        room_key = resolve_room_key(entry, topic, session=session, config=CONFIG)
        department_key = resolve_manager_department_key(
            session,
            entry.manager_name,
            config=CONFIG,
        )
        basis_key, basis_type = resolve_basis_for_protocol(
            entry,
            topic_key=topic_key,
            session=session,
            config=CONFIG,
        )
        return {
            "room_key": room_key,
            "department_key": department_key,
            "basis_key": basis_key,
            "basis_type": basis_type,
        }

    sync_fields = await asyncio.to_thread(_load_sync_fields)
    next_meeting_date = await resolve_next_meeting_date(entry, db)
    return {
        **sync_fields,
        "next_meeting_date": next_meeting_date,
        "is_series": is_series_entry(entry),
    }
