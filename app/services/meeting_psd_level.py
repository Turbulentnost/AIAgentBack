"""Совещания на уровне ПСД: флаг из 1С и обязательный участник."""

from __future__ import annotations

from typing import Any

from app.services.tasks_manager_resolver import (
    PSD_DELEGATED_MANAGER_EMAIL,
    PSD_DELEGATED_MANAGER_FIO,
)

PSD_LEVEL_HEADER_FIELD = "НаУровнеПСД"
PSD_LEVEL_PARTICIPANT_FIO = PSD_DELEGATED_MANAGER_FIO
PSD_LEVEL_PARTICIPANT_EMAIL = PSD_DELEGATED_MANAGER_EMAIL
PSD_LEVEL_PARTICIPANT_SOURCE = "psd_level"


def is_psd_level_value(value: Any) -> bool:
    text = str(value or "").strip().casefold().replace("ё", "е")
    return text in {"да", "yes", "true", "1"}


def is_psd_level_header(header: dict[str, Any] | None) -> bool:
    if not isinstance(header, dict):
        return False
    return is_psd_level_value(header.get(PSD_LEVEL_HEADER_FIELD))


def psd_level_known_emails() -> dict[str, str]:
    return {PSD_LEVEL_PARTICIPANT_FIO: PSD_LEVEL_PARTICIPANT_EMAIL}


def psd_level_participant_dict() -> dict[str, Any]:
    return {
        "full_name": PSD_LEVEL_PARTICIPANT_FIO,
        "email": PSD_LEVEL_PARTICIPANT_EMAIL,
        "ref_key": None,
        "department": None,
        "source": PSD_LEVEL_PARTICIPANT_SOURCE,
    }


def append_psd_level_participant_names(
    names: list[str],
    *,
    psd_level: bool,
) -> list[str]:
    if not psd_level:
        return list(names)
    merged = list(names)
    if PSD_LEVEL_PARTICIPANT_FIO not in merged:
        merged.append(PSD_LEVEL_PARTICIPANT_FIO)
    return merged


def append_psd_level_participants(
    participants: list[dict[str, Any]],
    *,
    psd_level: bool,
) -> list[dict[str, Any]]:
    if not psd_level:
        return list(participants)
    existing = {
        str(item.get("full_name") or item.get("ФИО") or "").strip()
        for item in participants
        if isinstance(item, dict)
    }
    if PSD_LEVEL_PARTICIPANT_FIO in existing:
        return list(participants)
    return [*participants, psd_level_participant_dict()]
