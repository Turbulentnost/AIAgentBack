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
from app.services.enterprise_positions_report import (
    enrich_person_from_positions_report,
    lookup_fios_by_position_title,
)
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


def _merge_participant_name_lists(*lists: list[str]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for source in lists:
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
            merged.append(normalized)
    return merged


def registry_participants_for_display(entry: Any) -> list[str]:
    """Подтверждённый состав для UI: колонка participants, без pending_add."""
    from_db = registry_participant_names(entry)
    if from_db:
        return from_db
    payload = entry.payload if isinstance(getattr(entry, "payload", None), dict) else {}
    stored = payload.get("occurrence_participant_names")
    if isinstance(stored, list) and stored:
        return _merge_participant_name_lists(stored)
    return []


def registry_participants_pending_target(entry: Any) -> list[str] | None:
    """Целевой состав из pending_add (ещё не применён в Outlook)."""
    payload = entry.payload if isinstance(getattr(entry, "payload", None), dict) else {}
    pending_add = payload.get("pending_add")
    if not isinstance(pending_add, dict):
        return None
    pending_names = pending_add.get("participants")
    if not isinstance(pending_names, list) or not pending_names:
        return None
    return _merge_participant_name_lists(pending_names)


def participant_names_from_outlook_attendees(
    entry: Any,
    *,
    seed_names: list[str],
) -> list[str] | None:
    """Восстанавливает ФИО по e-mail из Outlook, если в реестре участников меньше, чем в приглашении."""
    payload = entry.payload if isinstance(getattr(entry, "payload", None), dict) else {}
    payload_attendees = [
        email.strip()
        for email in (payload.get("attendees") or [])
        if isinstance(email, str) and email.strip()
    ]
    if len(payload_attendees) <= len(seed_names):
        return None

    try:
        attendee_entries = load_registry_outlook_attendee_entries(entry)
    except Exception:
        return None
    if not attendee_entries:
        return None

    email_to_display: dict[str, str] = {}
    for display_name, email in attendee_entries:
        key = email.strip().lower()
        if not key or key in email_to_display:
            continue
        label = (display_name or email).strip()
        email_to_display[key] = label or email.strip()

    result = list(seed_names)
    seen = {name.casefold() for name in result}
    for email in payload_attendees:
        label = email_to_display.get(email.lower())
        if not label:
            continue
        key = label.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(label)
    return result if len(result) > len(seed_names) else None


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


def fio_matches_calendar_display_name(fio: str, display_name: str | None) -> bool:
    if not display_name:
        return False
    surname = fio.split()[0].casefold() if fio.split() else ""
    if surname and surname in display_name.casefold():
        return True
    parts = [part for part in fio.split() if len(part) > 1]
    return bool(parts) and all(part.casefold() in display_name.casefold() for part in parts[:2])


def emails_for_name_in_calendar_attendees(
    name: str,
    attendee_entries: list[tuple[str | None, str]],
) -> list[str]:
    """E-mail участника по ФИО или должности среди уже приглашённых в Outlook."""
    normalized_name = name.strip()
    if not normalized_name:
        return []

    matched: list[str] = []
    seen: set[str] = set()
    name_key = normalized_name.casefold()

    for display_name, email in attendee_entries:
        key = email.lower()
        if key in seen:
            continue
        if display_name and display_name.strip().casefold() == name_key:
            seen.add(key)
            matched.append(email)

    if matched:
        return matched

    for fio in lookup_fios_by_position_title(normalized_name):
        for display_name, email in attendee_entries:
            key = email.lower()
            if key in seen:
                continue
            if fio_matches_calendar_display_name(fio, display_name):
                seen.add(key)
                matched.append(email)

    return matched


def load_registry_outlook_attendee_entries(entry: Any) -> list[tuple[str | None, str]]:
    """Участники текущего вхождения совещания из Outlook (без GAL)."""
    item_id = getattr(entry, "outlook_item_id", None)
    if not isinstance(item_id, str) or not item_id.strip():
        return []

    from app.tools.Outlook.cancel_meeting import get_meeting_by_id
    from app.tools.Outlook.send_meeting_invite import load_config
    from app.tools.Outlook.slot_search.attendees import (
        calendar_item_attendee_emails,
        calendar_item_attendee_entries,
    )

    changekey = getattr(entry, "outlook_changekey", None) or ""
    item = get_meeting_by_id(
        config=load_config(),
        item_id=item_id.strip(),
        changekey=changekey.strip() if isinstance(changekey, str) else "",
    )
    if not calendar_item_attendee_emails(item):
        try:
            item.refresh()
        except Exception:
            pass
    return calendar_item_attendee_entries(item)


def resolve_registry_participant_emails_from_outlook(
    entry: Any,
    names: list[str],
) -> dict[str, str]:
    """Сопоставляет ФИО/должности из реестра с e-mail текущего приглашения Outlook."""
    try:
        attendee_entries = load_registry_outlook_attendee_entries(entry)
    except Exception:
        return {}
    if not attendee_entries:
        return {}

    resolved: dict[str, str] = {}
    for name in names:
        emails = emails_for_name_in_calendar_attendees(name, attendee_entries)
        if emails:
            resolved[name.casefold()] = emails[0]
    return resolved
