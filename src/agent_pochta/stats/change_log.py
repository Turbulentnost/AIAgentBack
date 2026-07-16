"""Журнал изменений human-in-the-loop и связанных автоматических правок."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from agent_pochta.db.models import ChangeEventRow
from agent_pochta.stats.classification_log import (
    log_operator_department_event,
    log_operator_spam_event,
)
from agent_pochta.db.session import get_session_factory

EVENT_TYPES = frozenset(
    {
        "department_change",
        "spam_mark",
        "not_spam_mark",
        "restore_from_spam",
        "organization_change",
        "partner_change",
        "process_change",
        "routing_approve",
    }
)

_RESTORE_REASON_MARKER = "восстановлено из спама"


def _utc_now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _normalize_value(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _values_differ(old_value: Any, new_value: Any) -> bool:
    return _normalize_value(old_value) != _normalize_value(new_value)


def log_field_change(
    session: Session,
    *,
    message_id: str,
    event_type: str,
    field: str,
    old_value: Any = None,
    new_value: Any = None,
    email_id: uuid.UUID | None = None,
    actor: str = "operator",
    source: str = "system",
    created_at: datetime | None = None,
    force_changed: bool = False,
) -> ChangeEventRow | None:
    """Записывает одно событие изменения в change_events."""
    if event_type not in EVENT_TYPES:
        raise ValueError(f"Unknown event_type: {event_type}")
    if not _values_differ(old_value, new_value) and event_type != "routing_approve":
        return None

    row = ChangeEventRow(
        id=uuid.uuid4(),
        created_at=created_at or _utc_now_naive(),
        message_id=message_id,
        email_id=email_id,
        event_type=event_type,
        field=field,
        old_value=_normalize_value(old_value),
        new_value=_normalize_value(new_value),
        actor=actor,
        source=source,
    )
    session.add(row)
    session.flush()
    if event_type in {"department_change", "routing_approve"}:
        _mirror_department_to_classification(
            session,
            row=row,
            force_changed=force_changed or event_type == "department_change",
        )
    return row


def _mirror_department_to_classification(session: Session, *, row: ChangeEventRow, force_changed: bool = False) -> None:
    old_parts = str(row.old_value or "").split(" — ", 1)
    new_parts = str(row.new_value or "").split(" — ", 1)
    log_operator_department_event(
        session,
        message_id=row.message_id,
        email_id=row.email_id,
        original_department_id=old_parts[0] if old_parts else None,
        original_department_name=old_parts[1] if len(old_parts) > 1 else None,
        department_id=new_parts[0] if new_parts else "",
        department_name=new_parts[1] if len(new_parts) > 1 else None,
        source=row.source,
        force_changed=force_changed or row.event_type == "department_change",
    )


def _log_with_optional_session(
    session: Session | None,
    fn,
    *,
    commit: bool = False,
) -> ChangeEventRow | None:
    if session is not None:
        return fn(session)
    factory = get_session_factory()
    with factory() as own_session:
        result = fn(own_session)
        if commit and result is not None:
            own_session.commit()
        return result


def log_routing_correction(
    session: Session | None,
    *,
    message_id: str,
    email_id: uuid.UUID | None = None,
    original_department_id: str | None = None,
    original_department_name: str | None = None,
    department_id: str,
    department_name: str | None = None,
    actor: str = "operator",
    source: str = "learning:routing",
    force_changed: bool = False,
) -> ChangeEventRow | None:
    """department_change или routing_approve по сравнению отделов / ключевых полей."""

    def _write(db_session: Session) -> ChangeEventRow | None:
        old_dept = _normalize_value(original_department_id)
        new_dept = _normalize_value(department_id)
        dept_changed = bool(old_dept and new_dept and old_dept != new_dept)
        old_label = f"{original_department_id or department_id} — {original_department_name or department_name or ''}".strip(
            " —"
        )
        new_label = f"{department_id} — {department_name or department_id}".strip(" —")

        if dept_changed:
            return log_field_change(
                db_session,
                message_id=message_id,
                email_id=email_id,
                event_type="department_change",
                field="department_id",
                old_value=f"{original_department_id} — {original_department_name or ''}".strip(" —"),
                new_value=new_label,
                actor=actor,
                source=source,
                force_changed=True,
            )

        if force_changed:
            # Отдел тот же, но partner/organization изменены — только classification_events.
            log_operator_department_event(
                db_session,
                message_id=message_id,
                email_id=email_id,
                original_department_id=original_department_id,
                original_department_name=original_department_name,
                department_id=department_id,
                department_name=department_name,
                source=source,
                force_changed=True,
            )
            return None

        return log_field_change(
            db_session,
            message_id=message_id,
            email_id=email_id,
            event_type="routing_approve",
            field="routing",
            old_value=old_label,
            new_value=new_label,
            actor=actor,
            source=source,
            force_changed=False,
        )

    return _log_with_optional_session(session, _write, commit=session is None)


def log_spam_decision(
    session: Session | None,
    *,
    message_id: str,
    email_id: uuid.UUID | None = None,
    decision: str,
    reason: str | None = None,
    old_is_spam: bool | None = None,
    actor: str = "operator",
    source: str = "api:resolve-human",
) -> ChangeEventRow | None:
    """mark_spam / not_spam_mark / restore_from_spam по decision или reason."""

    def _write(db_session: Session) -> ChangeEventRow | None:
        reason_lower = (reason or "").lower()
        if decision == "mark_spam":
            event_type = "spam_mark"
            old_value, new_value = "not_spam", "spam"
        elif _RESTORE_REASON_MARKER in reason_lower:
            event_type = "restore_from_spam"
            old_value, new_value = "spam", "not_spam"
        else:
            event_type = "not_spam_mark"
            old_value, new_value = "spam", "not_spam"

        row = log_field_change(
            db_session,
            message_id=message_id,
            email_id=email_id,
            event_type=event_type,
            field="is_spam",
            old_value=old_value,
            new_value=new_value,
            actor=actor,
            source=source,
        )
        if row is not None:
            log_operator_spam_event(
                db_session,
                message_id=message_id,
                email_id=email_id,
                decision=decision,
                reason=reason,
                old_is_spam=old_is_spam,
                source=source,
            )
        return row

    return _log_with_optional_session(session, _write, commit=session is None)


def log_restore_from_spam(
    session: Session,
    *,
    message_id: str,
    email_id: uuid.UUID,
    actor: str = "operator",
    source: str = "api:restore-from-spam",
) -> ChangeEventRow | None:
    row = log_field_change(
        session,
        message_id=message_id,
        email_id=email_id,
        event_type="restore_from_spam",
        field="is_spam",
        old_value="spam",
        new_value="not_spam",
        actor=actor,
        source=source,
    )
    if row is not None:
        log_operator_spam_event(
            session,
            message_id=message_id,
            email_id=email_id,
            decision="mark_not_spam",
            reason="восстановлено из спама",
            old_is_spam=True,
            source=source,
        )
    return row


def log_department_resolution(
    session: Session,
    *,
    message_id: str,
    email_id: uuid.UUID,
    original_department_id: str | None,
    original_department_name: str | None,
    department_id: str,
    department_name: str | None,
    actor: str = "operator",
    source: str = "api:resolve-human",
    force_changed: bool = False,
) -> ChangeEventRow | None:
    return log_routing_correction(
        session,
        message_id=message_id,
        email_id=email_id,
        original_department_id=original_department_id,
        original_department_name=original_department_name,
        department_id=department_id,
        department_name=department_name,
        actor=actor,
        source=source,
        force_changed=force_changed,
    )


def log_xml_field_changes(
    session: Session | None,
    *,
    message_id: str,
    email_id: uuid.UUID | None,
    existing: dict[str, Any] | None,
    organization: str | None = None,
    partner: str | None = None,
    process: str | None = None,
    actor: str = "operator",
    source: str = "repository:rebuild_xml",
) -> list[ChangeEventRow]:
    """Фиксирует смену organization / partner / process при пересборке XML."""
    existing = existing or {}
    rows: list[ChangeEventRow] = []

    def _write(db_session: Session) -> list[ChangeEventRow]:
        local_rows: list[ChangeEventRow] = []
        checks = (
            ("organization_change", "organization", existing.get("organization"), organization),
            ("partner_change", "partner", existing.get("partner"), partner),
            (
                "process_change",
                "process",
                existing.get("process")
                or ((existing.get("services") or [{}])[0].get("process") if existing.get("services") else None),
                process,
            ),
        )
        for event_type, field, old_value, new_value in checks:
            if new_value is None:
                continue
            row = log_field_change(
                db_session,
                message_id=message_id,
                email_id=email_id,
                event_type=event_type,
                field=field,
                old_value=old_value,
                new_value=new_value,
                actor=actor,
                source=source,
            )
            if row is not None:
                local_rows.append(row)
        return local_rows

    if session is not None:
        rows.extend(_write(session))
    else:
        factory = get_session_factory()
        with factory() as own_session:
            rows.extend(_write(own_session))
            if rows:
                own_session.commit()
    return rows
