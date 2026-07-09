"""Тесты восстановления письма из спама."""

from __future__ import annotations

import json
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from agent_pochta.api.app import app
from agent_pochta.db.models import EmailMessageRow
from agent_pochta.routing.learning import learn_from_not_spam
from agent_pochta.rules.spam_learning import load_spam_learning, save_spam_pattern
from agent_pochta.schemas import EmailMessage, ProcessingStatus
from agent_pochta.workers.tasks import reprocess_message_task


@pytest.fixture
def learning_file(tmp_path: Path) -> Path:
    path = tmp_path / "spam_learning_patterns.json"
    path.write_text('{"version": "2.0", "entries": []}\n', encoding="utf-8")
    return path


def _email_row() -> EmailMessageRow:
    received_at = datetime.now(timezone.utc).replace(tzinfo=None)
    return EmailMessageRow(
        id=uuid.uuid4(),
        message_id="<restore@example>",
        received_at=received_at,
        mailbox="info@turbo-don.ru",
        sender_email="promo@spam-offers.xyz",
        sender_name="Promo",
        subject="Вебинар по продажам",
        status=ProcessingStatus.SPAM.value,
        spam_reason="Рекламная рассылка",
        is_spam=True,
        raw_payload_json=json.dumps(
            {
                "message_id": "<restore@example>",
                "mailbox": "info@turbo-don.ru",
                "sender_email": "promo@spam-offers.xyz",
                "subject": "Вебинар по продажам",
                "body_text": "Приглашаем на бесплатный вебинар только сегодня.",
                "received_at": received_at.isoformat(),
            },
            ensure_ascii=False,
        ),
    )


@contextmanager
def _mock_repo(row: EmailMessageRow):
    repo = MagicMock()
    repo.get_by_id.return_value = row
    repo.load_email_from_row.return_value = EmailMessage.model_validate(
        json.loads(row.raw_payload_json)
    )
    repo.learning_text_from_row.return_value = "Приглашаем на бесплатный вебинар только сегодня."

    def _mark_restored(row_id: uuid.UUID) -> EmailMessageRow | None:
        row.status = ProcessingStatus.AWAITING_HUMAN.value
        row.is_spam = False
        row.spam_reason = "Восстановлено из спама: требуется подтверждение оператора"
        row.human_review = True
        payload = json.loads(row.raw_payload_json or "{}")
        payload.pop("xml_document", None)
        row.raw_payload_json = json.dumps(payload, ensure_ascii=False)
        return row

    repo.mark_restored_from_spam.side_effect = _mark_restored

    session = MagicMock()
    session_factory = MagicMock()
    session_factory.return_value.__enter__.return_value = session

    with patch("agent_pochta.api.app.get_session_factory", return_value=session_factory):
        with patch("agent_pochta.api.app.EmailRepository", return_value=repo):
            yield repo, session


def test_restore_from_spam_endpoint_marks_awaiting_human_and_schedules_reprocess():
    row = _email_row()
    client = TestClient(app)
    task = MagicMock(id="task-restore")

    with _mock_repo(row) as (repo, _session):
        with patch("agent_pochta.api.app.learn_from_not_spam") as learn_not_spam:
            with patch("agent_pochta.api.app.reprocess_message_task") as reprocess_task:
                reprocess_task.delay.return_value = task
                response = client.post(f"/api/v1/email-messages/{row.id}/restore-from-spam")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == ProcessingStatus.AWAITING_HUMAN.value
    assert payload["id"] == str(row.id)
    assert payload["restored_from_spam"] is True
    learn_not_spam.assert_called_once()
    repo.mark_restored_from_spam.assert_called_once_with(row.id)
    reprocess_task.delay.assert_called_once_with(str(row.id), restored_from_spam=True)


def test_learn_from_not_spam_removes_pattern(
    learning_file: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("SPAM_LEARNING_PATH", str(learning_file))
    from agent_pochta.config import reset_settings

    reset_settings()
    save_spam_pattern(
        message_id="<restore@example>",
        sender_email="promo@spam-offers.xyz",
        subject="Вебинар по продажам",
        body="Приглашаем на бесплатный вебинар только сегодня.",
        spam_reason="Рекламная рассылка",
        path=learning_file,
    )

    result = learn_from_not_spam(
        message_id="<restore@example>",
        sender_email="promo@spam-offers.xyz",
        subject="Вебинар по продажам",
        body="Приглашаем на бесплатный вебинар только сегодня.",
        reason="Восстановлено из спама",
        path=learning_file,
    )
    assert result["spam_pattern_removed"] is True
    assert result["removed_count"] == 1
    assert result["antipattern_saved"] is True
    store = load_spam_learning(learning_file)
    assert len(store["entries"]) == 1
    assert store["entries"][0]["label"] == "not_spam"


def test_imap_listener_preserves_restored_from_spam_meta():
    from agent_pochta.nodes.n1_imap_listener import node_imap_listener
    from agent_pochta.services import build_container

    email = EmailMessage.model_validate(
        {
            "message_id": "<restore@example>",
            "mailbox": "info@turbo-don.ru",
            "sender_email": "vendor@example.com",
            "subject": "Test",
            "body_text": "Body",
            "received_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
        }
    )
    result = node_imap_listener(
        {"email": email, "meta": {"restored_from_spam": True}, "trace": []},
        build_container(),
    )
    assert result["meta"]["restored_from_spam"] is True
    assert result["meta"]["mailbox"] == email.mailbox


def test_reprocess_message_task_restored_from_spam_invokes_graph_with_meta(learning_file: Path):
    row = _email_row()
    graph = MagicMock()
    graph.invoke.return_value = {"status": ProcessingStatus.AWAITING_HUMAN}

    with patch("agent_pochta.workers.tasks.get_session_factory") as session_factory:
        session = MagicMock()
        session_factory.return_value.__enter__.return_value = session
        repo = MagicMock()
        repo.get_by_id.return_value = row
        repo.load_email_from_row.return_value = EmailMessage.model_validate(
            json.loads(row.raw_payload_json)
        )
        repo.learning_text_from_row.return_value = (
            "Приглашаем на бесплатный вебинар только сегодня."
        )
        with patch("agent_pochta.workers.tasks.EmailRepository", return_value=repo):
            with patch("agent_pochta.workers.tasks.learn_from_not_spam") as learn_not_spam:
                with patch("agent_pochta.workers.tasks.get_worker_graph", return_value=graph):
                    result = reprocess_message_task(str(row.id), restored_from_spam=True)

    learn_not_spam.assert_called_once_with(
        message_id=row.message_id,
        sender_email="promo@spam-offers.xyz",
        subject="Вебинар по продажам",
        body="Приглашаем на бесплатный вебинар только сегодня.",
        reason="Восстановлено из спама оператором",
    )
    repo.mark_restored_from_spam.assert_called_once_with(row.id)
    graph.invoke.assert_called_once()
    invoke_args = graph.invoke.call_args[0][0]
    assert invoke_args["meta"]["restored_from_spam"] is True
    assert result["ok"] is True
    assert result["status"] == ProcessingStatus.AWAITING_HUMAN.value
