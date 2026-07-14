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


def emails_for_resolved_participant_names(
    names: list[str],
    by_fio: dict[str, Any],
) -> list[str]:
    """E-mail целевого состава в порядке ФИО (без дублей по адресу)."""
    emails: list[str] = []
    seen: set[str] = set()
    for name in names:
        match = by_fio.get(name.casefold())
        email = getattr(match, "email", None) if match is not None else None
        if not isinstance(email, str) or not email.strip():
            continue
        key = email.strip().lower()
        if key in seen:
            continue
        seen.add(key)
        emails.append(email.strip())
    return emails


def registry_participant_names(entry: Any) -> list[str]:
    """ФИО участников совещания из колонки participants записи реестра."""
    source = entry.participants if isinstance(getattr(entry, "participants", None), list) else []
    names: list[str] = []
    seen: set[str] = set()
    for raw in source:
        if not isinstance(raw, str):
            continue
        normalized = raw.strip()
        if not normalized:
            continue
        key = normalized.casefold()
        if key in seen:
            continue
        seen.add(key)
        names.append(normalized)
    return names


def collect_attendees_from_registry_entry(
    entry: Any,
) -> list[tuple[str, str]]:
    """ФИО и роль для реестра: только entry.participants и роли из полей записи."""
    names = registry_participant_names(entry)
    initiator_key = (
        entry.initiator_name.strip().casefold()
        if isinstance(getattr(entry, "initiator_name", None), str) and entry.initiator_name.strip()
        else None
    )
    manager_key = (
        entry.manager_name.strip().casefold()
        if isinstance(getattr(entry, "manager_name", None), str) and entry.manager_name.strip()
        else None
    )

    attendees: list[tuple[str, str]] = []
    for name in names:
        key = name.casefold()
        if initiator_key and key == initiator_key:
            role = PRIORITY_INITIATOR
        elif manager_key and key == manager_key:
            role = PRIORITY_MANAGER
        else:
            role = PRIORITY_PARTICIPANT
        attendees.append((name, role))
    return attendees


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
    """Полный состав совещания: инициатор, руководитель и участники."""
    return attendee_fio_from_detail(detail)


def registry_attendee_sync_diff(entry: Any) -> tuple[list[str], list[str]]:
    """Разница между участниками в реестре и последним отправленным приглашением Outlook."""
    payload = getattr(entry, "payload", None) or {}
    if not isinstance(payload, dict):
        return [], []

    current_by_key: dict[str, str] = {}
    for email in payload.get("attendees") or []:
        if isinstance(email, str) and email.strip():
            current_by_key[email.strip().lower()] = email.strip()

    sent_payload = payload.get("sent_payload") or {}
    sent_source = sent_payload.get("attendees") if isinstance(sent_payload, dict) else None
    if not isinstance(sent_source, list) or not sent_source:
        return [], []

    sent_by_key: dict[str, str] = {}
    for email in sent_source:
        if isinstance(email, str) and email.strip():
            sent_by_key[email.strip().lower()] = email.strip()

    add = [current_by_key[key] for key in current_by_key if key not in sent_by_key]
    remove = [sent_by_key[key] for key in sent_by_key if key not in current_by_key]
    return add, remove
