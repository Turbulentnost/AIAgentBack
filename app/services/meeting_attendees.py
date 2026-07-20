from __future__ import annotations

from typing import Any


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
    return mapping


def collect_attendees_from_detail(
    detail: dict[str, Any],
) -> list[tuple[str, str]]:
    """ФИО и роль для проверки календарей: инициатор, руководитель, участники."""
    application = detail.get("application") or {}
    attendees: list[tuple[str, str]] = []
    seen: set[str] = set()

    def add(person: Any, role: str) -> None:
        name = _person_name(person)
        if not name or name in seen:
            return
        seen.add(name)
        attendees.append((name, role))

    add(application.get("initiator"), "initiator")
    add(application.get("manager"), "manager")
    for participant in application.get("participants") or []:
        add(participant, "participant")
    return attendees


def attendee_fio_from_detail(detail: dict[str, Any]) -> list[str]:
    return [name for name, _role in collect_attendees_from_detail(detail)]
