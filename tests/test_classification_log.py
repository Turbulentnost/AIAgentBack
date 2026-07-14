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
    log_agent_classification_from_row,
    log_classification_event,
    log_operator_department_event,
    log_operator_spam_event,
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
