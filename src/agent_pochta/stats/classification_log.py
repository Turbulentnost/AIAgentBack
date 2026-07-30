"""Журнал смены отдела и спам-статуса (classification_events) для графиков и точности."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

from sqlalchemy.orm import Session

from agent_pochta.db.models import ClassificationEventRow, EmailMessageRow
from agent_pochta.db.session import get_session_factory
from agent_pochta.state import AgentState

Category = Literal["department", "spam"]
Actor = Literal["agent", "operator"]

DEPARTMENT_EVENT_TYPES = frozenset(
    {
        "agent_assign",
        "agent_change",
        "operator_change",
        "operator_approve",
    }
)
SPAM_EVENT_TYPES = frozenset(
    {
        "agent_assign",
        "agent_change",
        "operator_mark_spam",
        "operator_mark_not_spam",
        "restore_from_spam",
    }
)


@dataclass(frozen=True)
class ClassificationSnapshot:
    department_id: str | None
    department_name: str | None
    is_spam: bool | None
    dept_confidence: float | None
    spam_confidence: float | None


def _utc_now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _normalize_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def operator_approval_fields_changed(
    *,
    old_department_id: str | None,
    new_department_id: str | None,
    old_partner: str | None = None,
    new_partner: str | None = None,
    old_organization: str | None = None,
    new_organization: str | None = None,
    compare_partner: bool = True,
    compare_organization: bool = True,
) -> bool:
    """True, если оператор изменил хотя бы одно ключевое поле перед сохранением."""
    if _normalize_text(old_department_id) != _normalize_text(new_department_id):
        return True
    if compare_partner and _normalize_text(old_partner) != _normalize_text(new_partner):
        return True
    if compare_organization and _normalize_text(old_organization) != _normalize_text(new_organization):
        return True
    return False


def operator_approval_rate(saved: int, changed: int) -> float | None:
    """Saved / (Saved + Changed); None, если одобрений оператора ещё не было."""
    total = int(saved) + int(changed)
    if total <= 0:
        return None
    return round(int(saved) / total, 4)


def snapshot_from_row(row: EmailMessageRow | None) -> ClassificationSnapshot | None:
    if row is None:
        return None
    return ClassificationSnapshot(
        department_id=_normalize_text(row.department_id),
        department_name=_normalize_text(row.department_name),
        is_spam=row.is_spam,
        dept_confidence=row.dept_confidence,
        spam_confidence=row.spam_confidence,
    )


def log_classification_event(
    session: Session,
    *,
    message_id: str,
    email_id: uuid.UUID | None,
    category: Category,
    event_type: str,
    actor: Actor,
    source: str,
    old_department_id: str | None = None,
    old_department_name: str | None = None,
    new_department_id: str | None = None,
    new_department_name: str | None = None,
    old_is_spam: bool | None = None,
    new_is_spam: bool | None = None,
    confidence: float | None = None,
    created_at: datetime | None = None,
) -> ClassificationEventRow | None:
    allowed = DEPARTMENT_EVENT_TYPES if category == "department" else SPAM_EVENT_TYPES
    if event_type not in allowed:
        raise ValueError(f"Unknown {category} event_type: {event_type}")

    old_dept = _normalize_text(old_department_id)
    new_dept = _normalize_text(new_department_id)
    if category == "department":
        # operator_approve / operator_change пишем даже при том же отделе
        # (partner/organization могли измениться → force_changed).
        if old_dept == new_dept and event_type not in {"operator_approve", "operator_change"}:
            return None
        if new_dept is None and event_type not in {"operator_approve", "operator_change"}:
            return None
    else:
        if old_is_spam is not None and new_is_spam is not None and old_is_spam == new_is_spam:
            return None
        if new_is_spam is None:
            return None

    row = ClassificationEventRow(
        id=uuid.uuid4(),
        created_at=created_at or _utc_now_naive(),
        message_id=message_id,
        email_id=email_id,
        category=category,
        event_type=event_type,
        old_department_id=old_dept,
        old_department_name=_normalize_text(old_department_name),
        new_department_id=new_dept,
        new_department_name=_normalize_text(new_department_name),
        old_is_spam=old_is_spam,
        new_is_spam=new_is_spam,
        confidence=confidence,
        actor=actor,
        source=source,
    )
    session.add(row)
    session.flush()
    return row


def log_agent_classification_from_row(
    session: Session,
    *,
    row: EmailMessageRow,
    before: ClassificationSnapshot | None,
    source: str = "node:finalize",
) -> list[ClassificationEventRow]:
    """Фиксирует автоматические решения агента при сохранении письма."""
    rows: list[ClassificationEventRow] = []
    after = snapshot_from_row(row)
    if after is None:
        return rows

    old_dept = before.department_id if before else None
    new_dept = after.department_id
    if new_dept and (old_dept != new_dept):
        event_type = "agent_assign" if old_dept is None else "agent_change"
        logged = log_classification_event(
            session,
            message_id=row.message_id,
            email_id=row.id,
            category="department",
            event_type=event_type,
            actor="agent",
            source=source,
            old_department_id=old_dept,
            old_department_name=before.department_name if before else None,
            new_department_id=new_dept,
            new_department_name=after.department_name,
            confidence=after.dept_confidence,
        )
        if logged is not None:
            rows.append(logged)

    old_spam = before.is_spam if before else None
    new_spam = after.is_spam
    if new_spam is not None and old_spam != new_spam:
        event_type = "agent_assign" if old_spam is None else "agent_change"
        logged = log_classification_event(
            session,
            message_id=row.message_id,
            email_id=row.id,
            category="spam",
            event_type=event_type,
            actor="agent",
            source=source,
            old_is_spam=old_spam,
            new_is_spam=new_spam,
            confidence=after.spam_confidence,
        )
        if logged is not None:
            rows.append(logged)

    return rows


def log_operator_department_event(
    session: Session,
    *,
    message_id: str,
    email_id: uuid.UUID | None,
    original_department_id: str | None,
    original_department_name: str | None,
    department_id: str,
    department_name: str | None,
    source: str = "api:resolve-human",
    force_changed: bool = False,
) -> ClassificationEventRow | None:
    old_dept = _normalize_text(original_department_id)
    new_dept = _normalize_text(department_id)
    dept_changed = bool(old_dept and new_dept and old_dept != new_dept)
    if dept_changed or force_changed:
        event_type = "operator_change"
    else:
        event_type = "operator_approve"
    return log_classification_event(
        session,
        message_id=message_id,
        email_id=email_id,
        category="department",
        event_type=event_type,
        actor="operator",
        source=source,
        old_department_id=original_department_id,
        old_department_name=original_department_name,
        new_department_id=department_id,
        new_department_name=department_name or department_id,
    )


def log_operator_spam_event(
    session: Session,
    *,
    message_id: str,
    email_id: uuid.UUID | None,
    decision: str,
    reason: str | None = None,
    old_is_spam: bool | None = None,
    source: str = "api:resolve-human",
) -> ClassificationEventRow | None:
    reason_lower = (reason or "").lower()
    if decision == "mark_spam":
        event_type = "operator_mark_spam"
        new_is_spam = True
    elif "восстановлено из спама" in reason_lower:
        event_type = "restore_from_spam"
        new_is_spam = False
    else:
        event_type = "operator_mark_not_spam"
        new_is_spam = False

    if old_is_spam is None:
        old_is_spam = not new_is_spam

    return log_classification_event(
        session,
        message_id=message_id,
        email_id=email_id,
        category="spam",
        event_type=event_type,
        actor="operator",
        source=source,
        old_is_spam=old_is_spam,
        new_is_spam=new_is_spam,
    )


def log_agent_classification_from_state(
    session: Session,
    row: EmailMessageRow,
    before: ClassificationSnapshot | None,
    state: AgentState,
    *,
    source: str = "node:finalize",
) -> list[ClassificationEventRow]:
    """Дополняет confidence из state, если в строке ещё не проставлено."""
    rows = log_agent_classification_from_row(session, row=row, before=before, source=source)
    if not rows:
        return rows

    spam = state.get("spam")
    routing = state.get("routing")
    for logged in rows:
        if logged.category == "spam" and logged.confidence is None and spam is not None:
            logged.confidence = spam.confidence
        if logged.category == "department" and logged.confidence is None and routing is not None:
            logged.confidence = routing.confidence
    session.flush()
    return rows


def collect_classification_summary(
    session: Session,
    *,
    start_utc: datetime,
    end_utc: datetime,
) -> dict[str, Any]:
    """Агрегаты для графиков и оценки точности за период."""
    rows = (
        session.query(ClassificationEventRow)
        .filter(
            ClassificationEventRow.created_at >= start_utc,
            ClassificationEventRow.created_at <= end_utc,
        )
        .order_by(ClassificationEventRow.created_at.asc())
        .all()
    )

    by_category: dict[str, int] = {"department": 0, "spam": 0}
    by_event_type: dict[str, int] = {}
    by_actor: dict[str, int] = {}
    entries: list[dict[str, Any]] = []

    agent_department_assigns = 0
    operator_department_corrections = 0
    agent_spam_assigns = 0
    operator_spam_corrections = 0
    operator_saved = 0
    operator_changed = 0

    for row in rows:
        by_category[row.category] = by_category.get(row.category, 0) + 1
        by_event_type[row.event_type] = by_event_type.get(row.event_type, 0) + 1
        by_actor[row.actor] = by_actor.get(row.actor, 0) + 1

        if row.category == "department" and row.event_type == "agent_assign":
            agent_department_assigns += 1
        elif row.category == "department" and row.event_type == "operator_change":
            operator_changed += 1
            if _normalize_text(row.old_department_id) != _normalize_text(row.new_department_id):
                operator_department_corrections += 1
        elif row.category == "department" and row.event_type == "operator_approve":
            operator_saved += 1
        elif row.category == "spam" and row.event_type == "agent_assign":
            agent_spam_assigns += 1
        elif row.category == "spam" and row.event_type in {
            "operator_mark_spam",
            "operator_mark_not_spam",
            "restore_from_spam",
        }:
            operator_spam_corrections += 1

        entries.append(
            {
                "id": str(row.id),
                "created_at": row.created_at.isoformat(sep=" "),
                "message_id": row.message_id,
                "email_id": str(row.email_id) if row.email_id else None,
                "category": row.category,
                "event_type": row.event_type,
                "old_department_id": row.old_department_id,
                "old_department_name": row.old_department_name,
                "new_department_id": row.new_department_id,
                "new_department_name": row.new_department_name,
                "old_is_spam": row.old_is_spam,
                "new_is_spam": row.new_is_spam,
                "confidence": row.confidence,
                "actor": row.actor,
                "source": row.source,
            }
        )

    department_accuracy = None
    if agent_department_assigns:
        department_accuracy = round(
            1.0 - operator_department_corrections / agent_department_assigns,
            4,
        )

    spam_accuracy = None
    if agent_spam_assigns:
        spam_accuracy = round(1.0 - operator_spam_corrections / agent_spam_assigns, 4)

    return {
        "source": "postgresql.classification_events",
        "total_events": len(rows),
        "by_category": dict(sorted(by_category.items())),
        "by_event_type": dict(sorted(by_event_type.items())),
        "by_actor": dict(sorted(by_actor.items())),
        "accuracy": {
            "agent_department_assigns": agent_department_assigns,
            "operator_department_corrections": operator_department_corrections,
            "department_accuracy": department_accuracy,
            "agent_spam_assigns": agent_spam_assigns,
            "operator_spam_corrections": operator_spam_corrections,
            "spam_accuracy": spam_accuracy,
        },
        "operator_approvals": build_operator_approvals(operator_saved, operator_changed),
        "events": entries,
    }


def build_operator_approvals(saved: int, changed: int) -> dict[str, Any]:
    """Счётчики сохранений оператора: saved без правок, changed с правками, rate."""
    return {
        "saved": int(saved),
        "changed": int(changed),
        "rate": operator_approval_rate(saved, changed),
    }


def collect_operator_approvals(
    session: Session,
    *,
    start_utc: datetime | None = None,
    end_utc: datetime | None = None,
) -> dict[str, Any]:
    """Агрегат operator_approve / operator_change из classification_events."""
    query = session.query(ClassificationEventRow).filter(
        ClassificationEventRow.category == "department",
        ClassificationEventRow.event_type.in_(("operator_approve", "operator_change")),
        ClassificationEventRow.actor == "operator",
    )
    if start_utc is not None:
        query = query.filter(ClassificationEventRow.created_at >= start_utc)
    if end_utc is not None:
        query = query.filter(ClassificationEventRow.created_at <= end_utc)

    saved = 0
    changed = 0
    for (event_type,) in query.with_entities(ClassificationEventRow.event_type).all():
        if event_type == "operator_approve":
            saved += 1
        elif event_type == "operator_change":
            changed += 1
    return build_operator_approvals(saved, changed)


def collect_classification_summary_for_period(
    *,
    start_utc: datetime,
    end_utc: datetime,
) -> dict[str, Any]:
    factory = get_session_factory()
    with factory() as session:
        return collect_classification_summary(session, start_utc=start_utc, end_utc=end_utc)
