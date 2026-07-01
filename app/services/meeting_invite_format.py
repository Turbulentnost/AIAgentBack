from __future__ import annotations

from typing import Any

INVITE_AGENT_FOOTER = "Совещание запланировано ИИ-агентом по планированию совещаний"


def resolve_invite_subject(
    detail: dict[str, Any] | None,
    *,
    override: str | None = None,
    fallback: str = "Совещание",
) -> str:
    explicit = (override or "").strip()
    if explicit:
        return explicit
    if not detail:
        return fallback
    application = detail.get("application") or {}
    for candidate in (
        detail.get("title"),
        application.get("agenda"),
    ):
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    number = detail.get("number")
    if isinstance(number, str) and number.strip():
        return f"Совещание {number.strip()}"
    return fallback


def manager_name_from_detail(detail: dict[str, Any] | None) -> str | None:
    if not detail:
        return None
    application = detail.get("application") or {}
    manager = application.get("manager")
    if isinstance(manager, dict):
        name = manager.get("full_name") or manager.get("Description")
        if isinstance(name, str) and name.strip():
            return name.strip()
    return None


def place_from_detail(detail: dict[str, Any] | None) -> str | None:
    if not detail:
        return None
    application = detail.get("application") or {}
    location = application.get("location")
    if isinstance(location, str) and location.strip():
        return location.strip()
    return None


MANAGER_LOCATION_PREFIX = "Руководитель совещания "


def format_invite_location(
    manager_name: str | None,
    place: str | None,
    *,
    override: str | None = None,
    fallback: str = "",
) -> str:
    manager = (manager_name or "").strip()
    place_text = (override or place or fallback or "").strip()
    if manager and place_text:
        return f"{MANAGER_LOCATION_PREFIX}{manager}, {place_text}"
    if manager:
        return f"{MANAGER_LOCATION_PREFIX}{manager}"
    if place_text:
        return place_text
    return ""


def place_from_invite_location(location: str | None) -> str | None:
    """Извлекает название переговорной из строки места приглашения."""
    text = (location or "").strip()
    if not text:
        return None
    if text.startswith(MANAGER_LOCATION_PREFIX):
        rest = text[len(MANAGER_LOCATION_PREFIX) :]
        if ", " in rest:
            place = rest.split(", ", 1)[1].strip()
            return place or None
        return None
    return text


def manager_name_from_memo_document(document: dict[str, Any] | None) -> str | None:
    if not document:
        return None
    application = document.get("application")
    if isinstance(application, dict):
        return manager_name_from_detail({"application": application})
    return None


def place_from_memo_document(document: dict[str, Any] | None) -> str | None:
    if not document:
        return None
    application = document.get("application")
    if isinstance(application, dict):
        return place_from_detail({"application": application})
    return None


def format_invite_location_from_detail(
    detail: dict[str, Any] | None,
    *,
    override: str | None = None,
    fallback: str = "",
) -> str:
    return format_invite_location(
        manager_name_from_detail(detail),
        place_from_detail(detail),
        override=override,
        fallback=fallback,
    )


def format_invite_body(
    attendees: list[tuple[str, str]],
    *,
    footer: str = INVITE_AGENT_FOOTER,
) -> str:
    """Тело приглашения: «ФИО <email>» построчно и подпись агента."""
    lines: list[str] = []
    for index, (fio, email) in enumerate(attendees):
        name = fio.strip()
        address = email.strip()
        suffix = ";" if index < len(attendees) - 1 else ""
        lines.append(f"{name} <{address}>{suffix}")
    if footer.strip():
        lines.extend(["", footer.strip()])
    return "\n".join(lines)


def invite_body_from_attendees(
    attendees: list[Any],
    *,
    footer: str = INVITE_AGENT_FOOTER,
) -> str:
    pairs: list[tuple[str, str]] = []
    for item in attendees:
        if isinstance(item, tuple) and len(item) == 2:
            fio, email = item
        else:
            fio = getattr(item, "fio", None) or (item.get("fio") if isinstance(item, dict) else "")
            email = getattr(item, "email", None) or (item.get("email") if isinstance(item, dict) else "")
        fio_text = str(fio or "").strip()
        email_text = str(email or "").strip()
        if fio_text and email_text:
            pairs.append((fio_text, email_text))
    return format_invite_body(pairs, footer=footer)
