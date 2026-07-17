from __future__ import annotations

from typing import Any

from app.tools.mail_templates import INVITE_AGENT_FOOTER, invite_agent_footer, render_mail_template
from app.tools.Outlook.meeting_rooms import resolve_room_by_name


def _memo_number_text(number: Any) -> str:
    if isinstance(number, str):
        return number.strip()
    if number is None:
        return ""
    return str(number).strip()


def _sz_subject_suffix(number: str) -> str:
    """Суффикс «СЗ {номер}» для темы приглашения; не дублирует префикс СЗ в номере."""
    raw = number.strip()
    if not raw:
        return ""
    if raw.upper().startswith("СЗ"):
        return f" {raw}"
    return f" СЗ {raw}"


def _subject_already_has_sz(subject: str, number: str) -> bool:
    """True, если в теме уже есть ссылка на этот номер СЗ."""
    topic = subject.strip()
    raw = number.strip()
    if not topic or not raw:
        return False
    upper = topic.upper()
    markers = [f"СЗ {raw}", f"СЗ{raw}", raw]
    return any(marker.upper() in upper for marker in markers if marker)


def append_sz_to_invite_subject(topic: str, number: str | None) -> str:
    """Добавляет «СЗ {номер}» сразу после темы совещания."""
    base = (topic or "").strip()
    raw = _memo_number_text(number)
    if not raw:
        return base
    if base and _subject_already_has_sz(base, raw):
        return base
    suffix = _sz_subject_suffix(raw)
    if not base:
        return suffix.strip()
    return f"{base}{suffix}"


def resolve_invite_subject(
    detail: dict[str, Any] | None,
    *,
    override: str | None = None,
    fallback: str = "Совещание",
) -> str:
    explicit = (override or "").strip()
    if explicit:
        return explicit
    number = _memo_number_text((detail or {}).get("number")) if detail else ""
    if not detail:
        return append_sz_to_invite_subject(fallback, number) if number else fallback
    application = detail.get("application") or {}
    for candidate in (
        detail.get("title"),
        application.get("agenda"),
    ):
        if isinstance(candidate, str) and candidate.strip():
            return append_sz_to_invite_subject(candidate.strip(), number)
    if number:
        return append_sz_to_invite_subject(fallback, number)
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


def resolve_room_for_location(location: str | None) -> dict[str, str] | None:
    """Возвращает переговорную из meeting_rooms.json по строке места в приглашении."""
    place = place_from_invite_location(location) or (location or "").strip()
    if not place:
        return None
    return resolve_room_by_name(place)


def format_invite_body(
    attendees: list[tuple[str, str]],
    *,
    room: dict[str, str] | None = None,
    footer: str | None = None,
) -> str:
    """Тело приглашения: «ФИО <email>» построчно и подпись агента."""
    footer_text = invite_agent_footer() if footer is None else footer
    pairs = list(attendees)
    if room and room.get("email"):
        pairs.append((str(room.get("name") or room["email"]).strip(), str(room["email"]).strip()))
    lines: list[str] = []
    for index, (fio, email) in enumerate(pairs):
        name = fio.strip()
        address = email.strip()
        suffix = ";" if index < len(pairs) - 1 else ""
        lines.append(f"{name} <{address}>{suffix}")
    participants_block = "\n".join(lines)
    if not footer_text.strip():
        return participants_block
    return render_mail_template(
        "invite_body",
        participants_block=participants_block,
        footer=footer_text.strip(),
    )


def invite_body_from_attendees(
    attendees: list[Any],
    *,
    room: dict[str, str] | None = None,
    footer: str | None = None,
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
    return format_invite_body(pairs, room=room, footer=footer)
