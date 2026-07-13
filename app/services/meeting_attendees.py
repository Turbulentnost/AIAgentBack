from __future__ import annotations

from typing import Any

from app.services.meeting_attendee_priority import (
    PRIORITY_INITIATOR,
    PRIORITY_MANAGER,
    PRIORITY_PARTICIPANT,
    is_required_priority_role,
    priority_role_label,
    resolve_priority_role,
    weight_for_priority_role,
)
from app.services.enterprise_positions_report import enrich_person_from_positions_report
from app.services.meeting_psd_level import (
    PSD_LEVEL_PARTICIPANT_FIO,
    psd_level_known_emails,
)


def _person_name(person: Any) -> str | None:
    if not isinstance(person, dict):
        return None
    name = person.get("full_name") or person.get("ФИО") or person.get("Description")
    if isinstance(name, str) and name.strip():
        return name.strip()
    return None


def _person_email(person: Any) -> str | None:
    if not isinstance(person, dict):
        return None
    email = person.get("email")
    if isinstance(email, str) and email.strip():
        return email.strip()
    return None


def _iter_priority_attendees_from_detail(
    detail: dict[str, Any],
) -> list[tuple[str, str, Any]]:
    application = detail.get("application") or {}
    attendees: list[tuple[str, str, Any]] = []
    seen: set[str] = set()

    def add(person: Any, base_role: str) -> None:
        name = _person_name(person)
        if not name or name in seen:
            return
        seen.add(name)
        enriched = enrich_person_from_positions_report(person) if isinstance(person, dict) else person
        priority_role = resolve_priority_role(base_role, enriched)
        attendees.append((name, priority_role, enriched))

    add(application.get("initiator"), PRIORITY_INITIATOR)
    add(application.get("manager"), PRIORITY_MANAGER)
    for participant in application.get("participants") or []:
        add(participant, PRIORITY_PARTICIPANT)
    return attendees


def person_from_detail_by_fio(detail: dict[str, Any], fio: str) -> dict[str, Any] | None:
    application = detail.get("application") or {}
    target = fio.strip()
    if not target:
        return None
    for key in ("initiator", "manager"):
        person = application.get(key)
        if isinstance(person, dict) and _person_name(person) == target:
            return person
    for participant in application.get("participants") or []:
        if isinstance(participant, dict) and _person_name(participant) == target:
            return participant
    return None


def emails_by_fio_from_detail(detail: dict[str, Any]) -> dict[str, str]:
    """E-mail из кэша detail (прогрев dashboard), без повторного запроса в 1С."""
    application = detail.get("application") or {}
    mapping: dict[str, str] = {}

    def add(person: Any) -> None:
        name = _person_name(person)
        email = _person_email(person)
        if name and email:
            mapping[name] = email

    add(application.get("initiator"))
    add(application.get("manager"))
    for participant in application.get("participants") or []:
        add(participant)
    if application.get("psd_level") or any(
        _person_name(participant) == PSD_LEVEL_PARTICIPANT_FIO
        for participant in application.get("participants") or []
    ):
        mapping.update(psd_level_known_emails())
    return mapping


def collect_attendees_from_detail(
    detail: dict[str, Any],
) -> list[tuple[str, str]]:
    """ФИО и приоритетная роль: инициатор, руководитель, директор, участник."""
    return [(name, priority_role) for name, priority_role, _enriched in _iter_priority_attendees_from_detail(detail)]


def attendee_priority_specs_from_detail(
    detail: dict[str, Any],
) -> list[dict[str, Any]]:
    """ФИО, priority_role, weight и required_for_slot для quorum-поиска."""
    return [
        {
            "fio": name,
            "priority_role": priority_role,
            "weight": weight_for_priority_role(priority_role, enriched),
            "required_for_slot": is_required_priority_role(priority_role),
            "role_label": priority_role_label(priority_role),
        }
        for name, priority_role, enriched in _iter_priority_attendees_from_detail(detail)
    ]


def attendee_fio_from_detail(detail: dict[str, Any]) -> list[str]:
    return [name for name, _role in collect_attendees_from_detail(detail)]


def participants_from_detail(detail: dict[str, Any]) -> list[str]:
    """Участники СЗ (без инициатора и руководителя) для реестра совещаний."""
    application = detail.get("application") or {}
    queue = detail.get("queue") or {}
    participants: list[str] = []
    seen: set[str] = set()

    for participant in application.get("participants") or []:
        if not isinstance(participant, dict):
            continue
        name = _person_name(participant)
        if not name:
            continue
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        participants.append(name)

    if participants:
        return participants

    for name in queue.get("participant_names") or []:
        if not isinstance(name, str):
            continue
        normalized = name.strip()
        if not normalized:
            continue
        key = normalized.casefold()
        if key in seen:
            continue
        seen.add(key)
        participants.append(normalized)

    return participants
