from __future__ import annotations

import asyncio
from typing import Any

from app.models.enums import MeetingRegistryStage

# 1С возвращает статусы слитно, например «НаИсполнении», без пробелов.
CONDUCTED_PROTOCOL_STATUS_KEYS = frozenset({"наисполнении"})
COMPLETED_PROTOCOL_STATUS_KEYS = frozenset({"закрыт", "закрыто"})

# Этапы реестра, после которых статус протокола в 1С больше не опрашиваем.
PROTOCOL_SYNC_SKIP_STAGES = frozenset(
    {
        MeetingRegistryStage.MEETING_COMPLETED,
        MeetingRegistryStage.CANCELLED,
    }
)

REGISTRY_CANCEL_BLOCKED_STAGES = frozenset(
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


def protocol_status_key(status: str | None) -> str | None:
    normalized = normalize_protocol_status(status)
    if not normalized:
        return None
    return normalized.replace("\u00a0", " ").replace(" ", "").casefold()


def protocol_status_is_terminal(status: str | None) -> bool:
    key = protocol_status_key(status)
    if not key:
        return False
    return key in CONDUCTED_PROTOCOL_STATUS_KEYS or key in COMPLETED_PROTOCOL_STATUS_KEYS


def should_fetch_protocol_status_from_onec(stage: MeetingRegistryStage) -> bool:
    return stage not in PROTOCOL_SYNC_SKIP_STAGES


def stage_for_protocol_status(status: str | None) -> MeetingRegistryStage | None:
    key = protocol_status_key(status)
    if not key:
        return None
    if key in CONDUCTED_PROTOCOL_STATUS_KEYS or key in COMPLETED_PROTOCOL_STATUS_KEYS:
        return MeetingRegistryStage.MEETING_COMPLETED
    return None


def registry_cancel_allowed(stage: MeetingRegistryStage) -> bool:
    return stage not in REGISTRY_CANCEL_BLOCKED_STAGES


def registry_actions_locked(stage: MeetingRegistryStage) -> bool:
    return stage in REGISTRY_CANCEL_BLOCKED_STAGES


async def fetch_protocol_status(ref_key: str) -> str | None:
    def _fetch() -> str | None:
        from app.tools.onec.connection import CONFIG, create_session
        from app.tools.onec.create_protocol import fetch_protocol_by_ref

        session = create_session(CONFIG)
        row = fetch_protocol_by_ref(session, CONFIG, ref_key)
        return normalize_protocol_status(row.get("Статус"))

    return await asyncio.to_thread(_fetch)
