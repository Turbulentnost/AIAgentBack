"""Тесты журнала change_events."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from agent_pochta.db.models import ChangeEventRow
from agent_pochta.stats.change_log import (
    log_field_change,
    log_routing_correction,
    log_spam_decision,
    log_xml_field_changes,
)


@pytest.fixture
def session() -> MagicMock:
    db = MagicMock()
    db.add = MagicMock()
    db.flush = MagicMock()
    return db


def test_log_department_change(session: MagicMock) -> None:
    row = log_field_change(
        session,
        message_id="<m1>",
        email_id=uuid.uuid4(),
        event_type="department_change",
        field="department_id",
        old_value="00-000001 — Старый",
        new_value="00-000002 — Новый",
        source="test",
    )
    assert row is not None
    assert row.event_type == "department_change"
    assert session.add.call_count == 2
    saved = session.add.call_args_list[0].args[0]
    assert isinstance(saved, ChangeEventRow)
    assert saved.old_value == "00-000001 — Старый"


def test_log_routing_approve_when_department_unchanged(session: MagicMock) -> None:
    row = log_routing_correction(
        session,
        message_id="<m2>",
        original_department_id="00-000044",
        original_department_name="Юридический",
        department_id="00-000044",
        department_name="Юридический",
        source="test",
    )
    assert row is not None
    assert row.event_type == "routing_approve"


def test_log_spam_mark(session: MagicMock) -> None:
    row = log_spam_decision(
        session,
        message_id="<m3>",
        decision="mark_spam",
        reason="Реклама",
    )
    assert row is not None
    assert row.event_type == "spam_mark"
    assert row.new_value == "spam"


def test_log_restore_from_spam_by_reason(session: MagicMock) -> None:
    row = log_spam_decision(
        session,
        message_id="<m4>",
        decision="mark_not_spam",
        reason="Восстановлено из спама оператором",
    )
    assert row is not None
    assert row.event_type == "restore_from_spam"


def test_log_xml_field_changes_only_when_changed(session: MagicMock) -> None:
    rows = log_xml_field_changes(
        session,
        message_id="<m5>",
        email_id=uuid.uuid4(),
        existing={"organization": "НП", "partner": "ООО А", "process": "исполнение"},
        organization="АЛ",
        partner="ООО А",
        process="исполнение",
    )
    assert len(rows) == 1
    assert rows[0].event_type == "organization_change"


def test_skip_unchanged_field(session: MagicMock) -> None:
    row = log_field_change(
        session,
        message_id="<m6>",
        event_type="partner_change",
        field="partner",
        old_value="Same",
        new_value="Same",
    )
    assert row is None
    session.add.assert_not_called()


def test_log_field_change_timestamp(session: MagicMock) -> None:
    ts = datetime(2026, 7, 8, 5, 35, 0, tzinfo=timezone.utc).replace(tzinfo=None)
    row = log_field_change(
        session,
        message_id="<m7>",
        event_type="process_change",
        field="process",
        old_value="исполнение",
        new_value="ознакомление",
        created_at=ts,
    )
    assert row is not None
    assert row.created_at == ts
