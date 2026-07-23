from __future__ import annotations

import asyncio
from typing import Any

from app.models.enums import MeetingRegistryStage

CONDUCTED_PROTOCOL_STATUSES = frozenset({"На исполнении"})
COMPLETED_PROTOCOL_STATUSES = frozenset({"Закрыт"})
TERMINAL_PROTOCOL_STATUSES = CONDUCTED_PROTOCOL_STATUSES | COMPLETED_PROTOCOL_STATUSES

# Этапы реестра, после которых статус протокола в 1С больше не опрашиваем.
PROTOCOL_SYNC_SKIP_STAGES = frozenset(
    {
        MeetingRegistryStage.PROTOCOL_CONDUCTED,
        MeetingRegistryStage.MEETING_COMPLETED,
        MeetingRegistryStage.CANCELLED,
    }
)


def normalize_protocol_status(raw: Any) -> str | None:
    if raw is None:
        return None
    text = str(raw).strip()
    return text or None


def protocol_status_is_terminal(status: str | None) -> bool:
    normalized = normalize_protocol_status(status)
    return bool(normalized and normalized in TERMINAL_PROTOCOL_STATUSES)


def should_fetch_protocol_status_from_onec(stage: MeetingRegistryStage) -> bool:
    return stage not in PROTOCOL_SYNC_SKIP_STAGES


def stage_for_protocol_status(status: str | None) -> MeetingRegistryStage | None:
    normalized = normalize_protocol_status(status)
    if not normalized:
        return None
    if normalized in COMPLETED_PROTOCOL_STATUSES:
        return MeetingRegistryStage.MEETING_COMPLETED
    if normalized in CONDUCTED_PROTOCOL_STATUSES:
        return MeetingRegistryStage.PROTOCOL_CONDUCTED
    return None


async def fetch_protocol_status(ref_key: str) -> str | None:
    def _fetch() -> str | None:
        from app.tools.onec.connection import CONFIG, create_session
        from app.tools.onec.create_protocol import fetch_protocol_by_ref

        session = create_session(CONFIG)
        row = fetch_protocol_by_ref(session, CONFIG, ref_key)
        return normalize_protocol_status(row.get("Статус"))

    return await asyncio.to_thread(_fetch)
