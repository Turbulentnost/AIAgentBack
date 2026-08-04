"""Восстановление зависших записей status=processing."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from agent_pochta.db.models import EmailMessageRow
from agent_pochta.db.repository import EmailRepository
from agent_pochta.schemas import ProcessingStatus
from agent_pochta.workers import tasks as worker_tasks


def _mock_session_with_rows(rows: list[EmailMessageRow], *, processing_count: int = 0):
    session = MagicMock()
    count_query = MagicMock()
    count_query.filter.return_value = count_query
    count_query.scalar.return_value = processing_count

    rows_query = MagicMock()
    rows_query.filter.return_value = rows_query
    rows_query.order_by.return_value = rows_query
    rows_query.limit.return_value = rows_query
    rows_query.all.return_value = rows

    def query_side_effect(*args, **kwargs):
        if args and "count" in str(args[0]).lower():
            return count_query
        return rows_query

    session.query.side_effect = query_side_effect
    return session


def _mock_settings(**overrides):
    settings = MagicMock()
    settings.stale_recovery_limit = overrides.get("stale_recovery_limit", 10)
    settings.processing_backlog_pause_threshold = overrides.get(
        "processing_backlog_pause_threshold", 9999
    )
    return settings


def _stale_row(*, message_id: str = "<stale@test>") -> EmailMessageRow:
    started = (datetime.utcnow() - timedelta(minutes=20)).isoformat()
    payload = {
        "message_id": message_id,
        "mailbox": "info@turbo-don.ru",
        "sender_email": "a@b.ru",
        "subject": "Тема",
        "body_text": "",
        "received_at": datetime.now(timezone.utc).isoformat(),
        "attachments": [],
        "processing_started_at": started,
    }
    return EmailMessageRow(
        id=uuid.uuid4(),
        message_id=message_id,
        received_at=datetime.now(timezone.utc),
        mailbox="info@turbo-don.ru",
        sender_email="a@b.ru",
        sender_name=None,
        subject="Тема",
        attachments_count=0,
        status=ProcessingStatus.PROCESSING.value,
        human_review=False,
        raw_payload_json=json.dumps(payload, ensure_ascii=False),
    )


def test_recover_stale_processing_reenqueues_old_rows():
    row = _stale_row()
    session = _mock_session_with_rows([row], processing_count=0)

    repo = EmailRepository(session)
    email = worker_tasks.EmailMessage(
        message_id=row.message_id,
        mailbox=row.mailbox,
        sender_email=row.sender_email,
        subject=row.subject,
        body_text="",
        received_at=row.received_at,
    )
    repo.load_email_from_row = MagicMock(return_value=email)
    repo.get_by_message_id = MagicMock(return_value=None)

    factory = MagicMock()
    factory.return_value.__enter__.return_value = session
    factory.return_value.__exit__.return_value = False

    with (
        patch.object(worker_tasks, "get_session_factory", return_value=factory),
        patch.object(worker_tasks, "get_settings", return_value=_mock_settings()),
        patch.object(worker_tasks, "EmailRepository", return_value=repo),
        patch.object(worker_tasks, "process_email_task") as mock_task,
    ):
        result = worker_tasks.recover_stale_processing(limit=10)

    assert result["recovered"] == 1
    assert result["skipped_fresh"] == 0
    mock_task.delay.assert_called_once()
    session.commit.assert_called_once()


def test_recover_stale_processing_skips_fresh_rows():
    row = _stale_row()
    fresh_started = datetime.utcnow().isoformat()
    payload = json.loads(row.raw_payload_json)
    payload["processing_started_at"] = fresh_started
    row.raw_payload_json = json.dumps(payload)

    session = _mock_session_with_rows([row], processing_count=0)

    factory = MagicMock()
    factory.return_value.__enter__.return_value = session
    factory.return_value.__exit__.return_value = False

    with (
        patch.object(worker_tasks, "get_session_factory", return_value=factory),
        patch.object(worker_tasks, "get_settings", return_value=_mock_settings()),
        patch.object(worker_tasks, "process_email_task") as mock_task,
    ):
        result = worker_tasks.recover_stale_processing(limit=10)

    assert result["recovered"] == 0
    assert result["skipped_fresh"] == 1
    mock_task.delay.assert_not_called()


def test_recover_stale_processing_skips_when_backlog_high():
    session = MagicMock()
    count_query = MagicMock()
    count_query.filter.return_value = count_query
    count_query.scalar.return_value = 120
    session.query.return_value = count_query

    factory = MagicMock()
    factory.return_value.__enter__.return_value = session
    factory.return_value.__exit__.return_value = False

    with (
        patch.object(worker_tasks, "get_session_factory", return_value=factory),
        patch.object(worker_tasks, "get_settings", return_value=_mock_settings(processing_backlog_pause_threshold=50)),
        patch.object(worker_tasks, "process_email_task") as mock_task,
    ):
        result = worker_tasks.recover_stale_processing(limit=10)

    assert result["recovered"] == 0
    assert result["skipped_backlog"] == 120
    mock_task.delay.assert_not_called()
