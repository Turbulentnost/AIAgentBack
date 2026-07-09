from __future__ import annotations

from typing import Any

from app.services.enterprise_positions_report import (
    enrich_person_from_positions_report,
    is_director_position_title,
    lookup_positions_by_fio,
    person_fio,
)

PRIORITY_INITIATOR = "initiator"
PRIORITY_MANAGER = "manager"
PRIORITY_DIRECTOR = "director"
PRIORITY_PARTICIPANT = "participant"
PRIORITY_ROOM = "room"

REQUIRED_PRIORITY_ROLES = frozenset({PRIORITY_INITIATOR, PRIORITY_MANAGER, PRIORITY_DIRECTOR})

ATTENDEE_ROLE_WEIGHTS: dict[str, float] = {
    PRIORITY_INITIATOR: 2.0,
    PRIORITY_MANAGER: 2.0,
    PRIORITY_DIRECTOR: 3.0,
    PRIORITY_PARTICIPANT: 1.0,
}

ATTENDEE_ROLE_LABELS = {
    PRIORITY_INITIATOR: "Инициатор",
    PRIORITY_MANAGER: "Руководитель",
    PRIORITY_DIRECTOR: "Директор",
    PRIORITY_PARTICIPANT: "Участник",
    PRIORITY_ROOM: "Переговорная",
}

_DIRECTOR_POSITION_MARKERS = (
    "директор",
    "director",
    "генеральный директор",
    "исполнительный директор",
    "заместитель генерального директора",
    "председатель совета директоров",
    "управляющий директор",
)


def _person_position_fields(person: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for key in ("position", "Должность", "Position"):
        value = person.get(key)
        if isinstance(value, str) and value.strip():
            values.append(value.strip())
    fio = person_fio(person)
    if fio:
        values.extend(lookup_positions_by_fio(fio))
    return values


def is_director_person(person: Any) -> bool:
    if not isinstance(person, dict):
        return False
    enriched = enrich_person_from_positions_report(person) or person
    if enriched.get("is_director") is True:
        return True
    if person.get("is_director") is True:
        return True
    explicit_role = person.get("priority_role") or person.get("role")
    if isinstance(explicit_role, str) and explicit_role.strip().casefold() in {
        PRIORITY_DIRECTOR,
        "директор",
    }:
        return True
    for position in _person_position_fields(enriched):
        if is_director_position_title(position):
            return True
    return False


def resolve_priority_role(base_role: str, person: Any) -> str:
    enriched = enrich_person_from_positions_report(person) if isinstance(person, dict) else person
    normalized = (base_role or PRIORITY_PARTICIPANT).strip().casefold()
    if normalized in {PRIORITY_INITIATOR, PRIORITY_MANAGER}:
        return normalized
    if normalized == PRIORITY_DIRECTOR:
        return PRIORITY_DIRECTOR
    if is_director_person(enriched):
        return PRIORITY_DIRECTOR
    return PRIORITY_PARTICIPANT


def weight_for_priority_role(priority_role: str, person: Any | None = None) -> float:
    role = (priority_role or PRIORITY_PARTICIPANT).strip().casefold()
    if person is not None and role in {PRIORITY_INITIATOR, PRIORITY_MANAGER} and is_director_person(person):
        return ATTENDEE_ROLE_WEIGHTS[PRIORITY_DIRECTOR]
    return ATTENDEE_ROLE_WEIGHTS.get(role, 1.0)


def is_required_priority_role(priority_role: str) -> bool:
    return priority_role.strip().casefold() in REQUIRED_PRIORITY_ROLES


def priority_role_label(priority_role: str) -> str:
    return ATTENDEE_ROLE_LABELS.get(priority_role.strip().casefold(), priority_role)
