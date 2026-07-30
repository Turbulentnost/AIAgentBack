"""Тесты Celery-задач human-in-the-loop."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from agent_pochta.db.models import EmailMessageRow
from agent_pochta.schemas import EmailMessage, ErpTaskResult, ProcessingStatus, Priority, RoutingResult
from agent_pochta.workers.tasks import continue_after_human_task, reprocess_message_task


def _processing_row(*, summary_ru: str | None = None) -> EmailMessageRow:
    received_at = datetime.now(timezone.utc).replace(tzinfo=None)
    return EmailMessageRow(
        id=uuid.uuid4(),
        message_id="<hitl-task@example>#info@turbo-don.ru",
        received_at=received_at,
        mailbox="info@turbo-don.ru",
        sender_email="vendor@example.com",
        sender_name="Vendor",
        subject="Акт сверки",
        status=ProcessingStatus.AWAITING_HUMAN.value,
        department_id="00-000044",
        department_name="Юридический отдел",
        summary_ru=summary_ru,
        raw_payload_json=json.dumps(
            {
                "message_id": "<hitl-task@example>#info@turbo-don.ru",
                "mailbox": "info@turbo-don.ru",
                "sender_email": "vendor@example.com",
                "subject": "Акт сверки",
                "body_text": "Просим подписать акт сверки.",
                "received_at": received_at.isoformat(),
                "routing_recipient": "info@turbo-don.ru",
                "xml_document": "<document></document>",
            },
            ensure_ascii=False,
        ),
    )


def test_continue_after_human_task_passes_existing_summary(monkeypatch: pytest.MonkeyPatch):
    row = _processing_row(summary_ru="Обзор уже есть.")
    session = MagicMock()
    repo = MagicMock()
    repo.get_by_id.return_value = row
    repo.load_email_from_row.return_value = MagicMock()
    repo.build_routing_from_row.return_value = MagicMock()

    session_factory = MagicMock()
    session_factory.return_value.__enter__.return_value = session

    approval = MagicMock(
        return_value={
            "status": ProcessingStatus.DONE,
            "summary_ru": "Обзор уже есть.",
            "trace": ["human_approved", "create_erp_task", "finalize"],
        }
    )

    monkeypatch.setattr("agent_pochta.workers.tasks.get_session_factory", lambda: session_factory)
    monkeypatch.setattr("agent_pochta.workers.tasks.EmailRepository", lambda _s: repo)
    monkeypatch.setattr("agent_pochta.workers.runtime.get_worker_container", lambda: MagicMock())
    monkeypatch.setattr("agent_pochta.workers.hitl.continue_after_human_approval", approval)

    task = continue_after_human_task
    task.push_request(retries=0, max_retries=2)
    try:
        result = task(str(row.id))
    finally:
        task.pop_request()

    assert result["ok"] is True
    approval.assert_called_once()
    kwargs = approval.call_args.kwargs
    assert kwargs["summary_ru"] == "Обзор уже есть."
    assert kwargs["meta"]["xml_document"] == "<document></document>"


def test_continue_after_human_task_fails_without_reverting_approval(monkeypatch: pytest.MonkeyPatch):
    row = _processing_row(summary_ru="Обзор уже есть.")
    row.status = ProcessingStatus.PROCESSING.value

    session = MagicMock()
    repo = MagicMock()
    repo.get_by_id.return_value = row
    repo.load_email_from_row.return_value = MagicMock()
    repo.build_routing_from_row.return_value = MagicMock()

    session_factory = MagicMock()
    session_factory.return_value.__enter__.return_value = session

    monkeypatch.setattr("agent_pochta.workers.tasks.get_session_factory", lambda: session_factory)
    monkeypatch.setattr("agent_pochta.workers.tasks.EmailRepository", lambda _s: repo)
    monkeypatch.setattr("agent_pochta.workers.runtime.get_worker_container", lambda: MagicMock())
    monkeypatch.setattr(
        "agent_pochta.workers.hitl.continue_after_human_approval",
        MagicMock(side_effect=RuntimeError("timed out")),
    )

    task = continue_after_human_task
    task.push_request(retries=2, max_retries=2)
    try:
        result = task(str(row.id))
    finally:
        task.pop_request()

    assert result["ok"] is False
    assert row.status == ProcessingStatus.ERROR.value
    assert row.human_review is False


def test_continue_after_human_task_syncs_done_status(monkeypatch: pytest.MonkeyPatch):
    row = _processing_row(summary_ru="Обзор уже есть.")
    row.status = ProcessingStatus.PROCESSING.value

    session = MagicMock()
    repo = MagicMock()
    repo.get_by_id.return_value = row
    repo.load_email_from_row.return_value = EmailMessage(
        message_id=row.message_id,
        mailbox=row.mailbox,
        sender_email=row.sender_email,
        subject=row.subject or "Акт сверки",
        received_at=datetime.now(timezone.utc),
    )
    repo.build_routing_from_row.return_value = RoutingResult(
        department_id=row.department_id,
        department_name=row.department_name,
        confidence=1.0,
        reasoning="Подтверждено оператором",
        priority=Priority.NORMAL,
    )

    session_factory = MagicMock()
    session_factory.return_value.__enter__.return_value = session

    approval_result = {
        "status": ProcessingStatus.DONE,
        "summary_ru": "Обзор уже есть.",
        "erp": ErpTaskResult(success=True, erp_document_number="ВК-001", erp_task_id="guid"),
        "trace": ["human_approved", "create_erp_task", "finalize"],
    }

    monkeypatch.setattr("agent_pochta.workers.tasks.get_session_factory", lambda: session_factory)
    monkeypatch.setattr("agent_pochta.workers.tasks.EmailRepository", lambda _s: repo)
    monkeypatch.setattr("agent_pochta.workers.runtime.get_worker_container", lambda: MagicMock())
    monkeypatch.setattr(
        "agent_pochta.workers.hitl.continue_after_human_approval",
        MagicMock(return_value=approval_result),
    )

    task = continue_after_human_task
    task.push_request(retries=0, max_retries=2)
    try:
        result = task(str(row.id))
    finally:
        task.pop_request()

    assert result["ok"] is True
    assert row.status == ProcessingStatus.DONE.value
    assert row.erp_document_number == "ВК-001"
    assert row.processed_at is not None


def test_continue_after_human_erp_failure_stays_done(monkeypatch: pytest.MonkeyPatch):
    from agent_pochta.schemas import ErpTaskResult

    row = _processing_row(summary_ru="Обзор уже есть.")
    row.status = ProcessingStatus.PROCESSING.value

    session = MagicMock()
    repo = MagicMock()
    repo.get_by_id.return_value = row
    email = EmailMessage(
        message_id=row.message_id,
        mailbox=row.mailbox,
        sender_email=row.sender_email,
        subject=row.subject or "Акт сверки",
        received_at=datetime.now(timezone.utc),
    )
    repo.load_email_from_row.return_value = email
    repo.build_routing_from_row.return_value = RoutingResult(
        department_id=row.department_id,
        department_name=row.department_name,
        confidence=1.0,
        reasoning="Подтверждено оператором",
        priority=Priority.NORMAL,
    )

    session_factory = MagicMock()
    session_factory.return_value.__enter__.return_value = session

    approval_result = {
        "status": ProcessingStatus.DONE,
        "summary_ru": "Обзор уже есть.",
        "erp": ErpTaskResult(success=False, error="1C timeout"),
        "meta": {"erp_retry_scheduled": True},
        "trace": ["human_approved", "create_erp_task", "finalize"],
    }

    schedule = MagicMock()
    monkeypatch.setattr("agent_pochta.workers.tasks.get_session_factory", lambda: session_factory)
    monkeypatch.setattr("agent_pochta.workers.tasks.EmailRepository", lambda _s: repo)
    monkeypatch.setattr("agent_pochta.workers.runtime.get_worker_container", lambda: MagicMock())
    monkeypatch.setattr("agent_pochta.workers.tasks._schedule_erp_retry", schedule)
    monkeypatch.setattr(
        "agent_pochta.workers.hitl.continue_after_human_approval",
        MagicMock(return_value=approval_result),
    )

    task = continue_after_human_task
    task.push_request(retries=0, max_retries=2)
    try:
        result = task(str(row.id))
    finally:
        task.pop_request()

    assert result["ok"] is True
    assert row.status == ProcessingStatus.DONE.value
    schedule.assert_called_once_with(email.message_id)


def test_reprocess_message_task_fails_with_error_status(monkeypatch: pytest.MonkeyPatch):
    row = _processing_row()
    row.status = ProcessingStatus.PROCESSING.value

    session = MagicMock()
    repo = MagicMock()
    repo.get_by_id.return_value = row
    repo.load_email_from_row.return_value = MagicMock()

    session_factory = MagicMock()
    session_factory.return_value.__enter__.return_value = session

    graph = MagicMock()
    graph.invoke.side_effect = RuntimeError("timed out")

    monkeypatch.setattr("agent_pochta.workers.tasks.get_session_factory", lambda: session_factory)
    monkeypatch.setattr("agent_pochta.workers.tasks.EmailRepository", lambda _s: repo)
    monkeypatch.setattr("agent_pochta.workers.tasks.get_worker_graph", lambda: graph)

    task = reprocess_message_task
    task.push_request(retries=2, max_retries=2)
    try:
        result = task(str(row.id))
    finally:
        task.pop_request()

    assert result["ok"] is False
    assert row.status == ProcessingStatus.ERROR.value
    assert row.human_review is True
