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
    session = MagicMock()
    query = MagicMock()
    session.query.return_value = query
    query.filter.return_value = query
    query.order_by.return_value = query
    query.limit.return_value = query
    query.all.return_value = [row]

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

    factory = MagicMock()
    factory.return_value.__enter__.return_value = session
    factory.return_value.__exit__.return_value = False

    with (
        patch.object(worker_tasks, "get_session_factory", return_value=factory),
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

    session = MagicMock()
    query = MagicMock()
    session.query.return_value = query
    query.filter.return_value = query
    query.order_by.return_value = query
    query.limit.return_value = query
    query.all.return_value = [row]

    factory = MagicMock()
    factory.return_value.__enter__.return_value = session
    factory.return_value.__exit__.return_value = False

    with (
        patch.object(worker_tasks, "get_session_factory", return_value=factory),
        patch.object(worker_tasks, "process_email_task") as mock_task,
    ):
        result = worker_tasks.recover_stale_processing(limit=10)

    assert result["recovered"] == 0
    assert result["skipped_fresh"] == 1
    mock_task.delay.assert_not_called()
