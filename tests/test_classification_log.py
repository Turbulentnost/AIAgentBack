"""Тесты журнала classification_events."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agent_pochta.db.models import Base, ClassificationEventRow, EmailMessageRow
from agent_pochta.stats.classification_log import (
    collect_classification_summary,
    collect_operator_approvals,
    log_agent_classification_from_row,
    log_classification_event,
    log_operator_department_event,
    log_operator_spam_event,
    operator_approval_fields_changed,
    operator_approval_rate,
    snapshot_from_row,
)


@pytest.fixture
def session() -> MagicMock:
    db = MagicMock()
    db.add = MagicMock()
    db.flush = MagicMock()
    return db


def test_log_operator_department_change(session: MagicMock) -> None:
    row = log_operator_department_event(
        session,
        message_id="<m1>",
        email_id=uuid.uuid4(),
        original_department_id="00-000001",
        original_department_name="Старый",
        department_id="00-000002",
        department_name="Новый",
    )
    assert row is not None
    assert row.category == "department"
    assert row.event_type == "operator_change"
    assert row.actor == "operator"


def test_log_operator_department_approve(session: MagicMock) -> None:
    row = log_operator_department_event(
        session,
        message_id="<m2>",
        email_id=uuid.uuid4(),
        original_department_id="00-000044",
        original_department_name="Юридический",
        department_id="00-000044",
        department_name="Юридический",
    )
    assert row is not None
    assert row.event_type == "operator_approve"


def test_log_operator_department_force_changed_same_dept(session: MagicMock) -> None:
    row = log_operator_department_event(
        session,
        message_id="<m2b>",
        email_id=uuid.uuid4(),
        original_department_id="00-000044",
        original_department_name="Юридический",
        department_id="00-000044",
        department_name="Юридический",
        force_changed=True,
    )
    assert row is not None
    assert row.event_type == "operator_change"


def test_operator_approval_fields_changed_department() -> None:
    assert operator_approval_fields_changed(
        old_department_id="00-000001",
        new_department_id="00-000002",
        old_partner="ООО А",
        new_partner="ООО А",
        old_organization="НП",
        new_organization="НП",
    )


def test_operator_approval_fields_changed_partner_only() -> None:
    assert operator_approval_fields_changed(
        old_department_id="00-000044",
        new_department_id="00-000044",
        old_partner="ООО А",
        new_partner="ООО Б",
        old_organization="НП",
        new_organization="НП",
    )


def test_operator_approval_fields_unchanged() -> None:
    assert not operator_approval_fields_changed(
        old_department_id="00-000044",
        new_department_id="00-000044",
        old_partner="ООО А",
        new_partner="ООО А",
        old_organization="НП",
        new_organization="НП",
    )


def test_operator_approval_rate_formula() -> None:
    assert operator_approval_rate(8, 2) == 0.8
    assert operator_approval_rate(0, 0) is None
    assert operator_approval_rate(0, 5) == 0.0
    assert operator_approval_rate(3, 0) == 1.0


def test_log_operator_spam_mark(session: MagicMock) -> None:
    row = log_operator_spam_event(
        session,
        message_id="<m3>",
        email_id=uuid.uuid4(),
        decision="mark_spam",
        old_is_spam=False,
    )
    assert row is not None
    assert row.category == "spam"
    assert row.event_type == "operator_mark_spam"
    assert row.new_is_spam is True


def test_log_agent_department_assign(session: MagicMock) -> None:
    row = EmailMessageRow(
        id=uuid.uuid4(),
        message_id="<agent@example>",
        received_at=datetime.now(timezone.utc).replace(tzinfo=None),
        mailbox="info@turbo-don.ru",
        sender_email="vendor@example.com",
        department_id="00-000065",
        department_name="ОМТО",
        dept_confidence=0.91,
        is_spam=False,
    )
    logged = log_agent_classification_from_row(session, row=row, before=None)
    assert len(logged) == 2
    assert logged[0].event_type == "agent_assign"
    assert logged[0].category == "department"
    assert logged[1].category == "spam"


def test_skip_unchanged_agent_department(session: MagicMock) -> None:
    before = snapshot_from_row(
        EmailMessageRow(
            id=uuid.uuid4(),
            message_id="<same@example>",
            received_at=datetime.now(timezone.utc).replace(tzinfo=None),
            mailbox="info@turbo-don.ru",
            sender_email="vendor@example.com",
            department_id="00-000065",
            department_name="ОМТО",
            is_spam=True,
        )
    )
    row = EmailMessageRow(
        id=uuid.uuid4(),
        message_id="<same@example>",
        received_at=datetime.now(timezone.utc).replace(tzinfo=None),
        mailbox="info@turbo-don.ru",
        sender_email="vendor@example.com",
        department_id="00-000065",
        department_name="ОМТО",
        is_spam=True,
    )
    logged = log_agent_classification_from_row(session, row=row, before=before)
    assert logged == []


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    try:
        yield db
    finally:
        db.close()


def test_collect_classification_summary_accuracy(db_session) -> None:
    email_id = uuid.uuid4()
    ts = datetime(2026, 7, 8, 10, 0, 0)
    for event_type, category, actor in (
        ("agent_assign", "department", "agent"),
        ("operator_change", "department", "operator"),
        ("agent_assign", "spam", "agent"),
        ("operator_mark_not_spam", "spam", "operator"),
    ):
        log_classification_event(
            db_session,
            message_id="<summary@test>",
            email_id=email_id,
            category=category,
            event_type=event_type,
            actor=actor,
            source="test",
            new_department_id="00-000001" if category == "department" else None,
            new_department_name="Отдел" if category == "department" else None,
            old_department_id="00-000002" if event_type == "operator_change" else None,
            new_is_spam=False if category == "spam" else None,
            old_is_spam=True if event_type == "operator_mark_not_spam" else None,
            created_at=ts,
        )
    db_session.commit()

    summary = collect_classification_summary(
        db_session,
        start_utc=datetime(2026, 7, 8, 0, 0, 0),
        end_utc=datetime(2026, 7, 9, 0, 0, 0),
    )
    assert summary["total_events"] == 4
    assert summary["accuracy"]["agent_department_assigns"] == 1
    assert summary["accuracy"]["operator_department_corrections"] == 1
    assert summary["accuracy"]["department_accuracy"] == 0.0
    assert summary["accuracy"]["spam_accuracy"] == 0.0
    assert summary["operator_approvals"] == {"saved": 0, "changed": 1, "rate": 0.0}


def test_collect_operator_approvals_saved_changed_rate(db_session) -> None:
    email_id = uuid.uuid4()
    ts = datetime(2026, 7, 8, 12, 0, 0)
    for event_type, old_dept, new_dept in (
        ("operator_approve", "00-000044", "00-000044"),
        ("operator_approve", "00-000065", "00-000065"),
        ("operator_change", "00-000044", "00-000065"),
        # partner/org-only change: same dept codes, but counted as changed
        ("operator_change", "00-000001", "00-000001"),
    ):
        log_classification_event(
            db_session,
            message_id="<approvals@test>",
            email_id=email_id,
            category="department",
            event_type=event_type,
            actor="operator",
            source="test",
            old_department_id=old_dept,
            old_department_name="A",
            new_department_id=new_dept,
            new_department_name="B",
            created_at=ts,
        )
    db_session.commit()

    approvals = collect_operator_approvals(
        db_session,
        start_utc=datetime(2026, 7, 8, 0, 0, 0),
        end_utc=datetime(2026, 7, 9, 0, 0, 0),
    )
    assert approvals == {"saved": 2, "changed": 2, "rate": 0.5}

    summary = collect_classification_summary(
        db_session,
        start_utc=datetime(2026, 7, 8, 0, 0, 0),
        end_utc=datetime(2026, 7, 9, 0, 0, 0),
    )
    # Partner-only operator_change with same dept must not inflate department corrections.
    assert summary["accuracy"]["operator_department_corrections"] == 1
    assert summary["operator_approvals"] == approvals


def test_log_routing_correction_force_changed_partner_only(session: MagicMock) -> None:
    from agent_pochta.stats.change_log import log_routing_correction

    row = log_routing_correction(
        session,
        message_id="<m-partner>",
        email_id=uuid.uuid4(),
        original_department_id="00-000044",
        original_department_name="Юридический",
        department_id="00-000044",
        department_name="Юридический",
        force_changed=True,
        source="test",
    )
    assert row is None
    classified = session.add.call_args.args[0]
    assert isinstance(classified, ClassificationEventRow)
    assert classified.event_type == "operator_change"
