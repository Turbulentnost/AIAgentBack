"""Тесты согласованности label/reason при обучении спам-фильтра."""

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
from agent_pochta.routing.learning import learn_from_spam_mark
from agent_pochta.rules.spam_learning import (
    _normalize_entry,
    _reconcile_label_with_reason,
    load_spam_learning,
    reason_indicates_not_spam,
    resolve_human_spam_reason,
    save_spam_pattern,
)
from agent_pochta.schemas import EmailMessage, ProcessingStatus


@pytest.fixture
def learning_file(tmp_path: Path) -> Path:
    return tmp_path / "spam_learning_patterns.json"


def test_reason_indicates_not_spam_detects_llm_phrases():
    assert reason_indicates_not_spam("Нет признаков спама: деловой запрос")
    assert reason_indicates_not_spam("Не спам — confidence 98%")
    assert reason_indicates_not_spam("Внешний домен, но не реклама и не фишинг")
    assert not reason_indicates_not_spam("Рекламная рассылка")
    assert not reason_indicates_not_spam("")


def test_resolve_human_spam_reason_rejects_llm_not_spam_text():
    assert resolve_human_spam_reason(None) == "Отмечено офис-менеджером"
    assert resolve_human_spam_reason("") == "Отмечено офис-менеджером"
    assert resolve_human_spam_reason("Не спам — confidence 98%") == "Отмечено офис-менеджером"
    assert resolve_human_spam_reason("Рекламная рассылка") == "Рекламная рассылка"


def test_resolve_human_spam_reason_rejects_routing_escalation_text():
    assert (
        resolve_human_spam_reason("Низкая уверенность маршрута (НИЗКАЯ, score=45)")
        == "Отмечено офис-менеджером"
    )
    assert (
        resolve_human_spam_reason("Конфликт нескольких правил маршрутизации")
        == "Отмечено офис-менеджером"
    )


def test_save_spam_pattern_sanitizes_contradictory_reason(
    learning_file: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("SPAM_LEARNING_PATH", str(learning_file))
    from agent_pochta.config import reset_settings

    reset_settings()

    save_spam_pattern(
        message_id="<spam@example>",
        sender_email="vendor@example.com",
        subject="Проектная документация",
        body="Просим рассмотреть проект.",
        spam_reason="Нет признаков спама: деловой запрос",
        path=learning_file,
    )

    store = load_spam_learning(learning_file)
    assert len(store["entries"]) == 1
    assert store["entries"][0]["label"] == "spam"
    assert store["entries"][0]["reason"] == "Отмечено офис-менеджером"


def test_learn_from_spam_mark_sanitizes_llm_not_spam_reason(
    learning_file: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("SPAM_LEARNING_PATH", str(learning_file))
    monkeypatch.setenv("RAG_BACKEND", "stub")
    from agent_pochta.config import reset_settings

    reset_settings()

    learn_from_spam_mark(
        message_id="<spam@example>",
        sender_email="vendor@example.com",
        subject="Проектная документация",
        body="Просим рассмотреть проект.",
        spam_reason="Нет признаков спама: деловой запрос",
        path=learning_file,
    )

    store = load_spam_learning(learning_file)
    assert store["entries"][0]["label"] == "spam"
    assert store["entries"][0]["reason"] == "Отмечено офис-менеджером"


def test_normalize_entry_reconciles_contradictory_label(
    learning_file: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("SPAM_LEARNING_PATH", str(learning_file))
    from agent_pochta.config import reset_settings

    reset_settings()

    raw = {
        "id": "test-id",
        "label": "spam",
        "reason": "Нет признаков спама: деловой запрос от внешнего контрагента",
    }
    normalized = _normalize_entry(raw)
    assert normalized["label"] == "not_spam"
    assert _reconcile_label_with_reason("spam", raw["reason"]) == "not_spam"


def test_spam_learning_json_has_no_label_reason_contradictions():
    path = Path(__file__).resolve().parents[1] / "data" / "spam_learning_patterns.json"
    if not path.is_file():
        pytest.skip("spam_learning_patterns.json not present")
    store = load_spam_learning(path)
    for entry in store.get("entries") or []:
        label = entry.get("label")
        reason = entry.get("reason") or ""
        if label == "spam":
            assert not reason_indicates_not_spam(reason), (
                f"Entry {entry.get('id')} has label=spam but not-spam reason: {reason[:80]}"
            )


def _email_row(*, status: str, spam_reason: str | None = None) -> EmailMessageRow:
    received_at = datetime.now(timezone.utc).replace(tzinfo=None)
    return EmailMessageRow(
        id=uuid.uuid4(),
        message_id="<resolve@example>",
        received_at=received_at,
        mailbox="info@turbo-don.ru",
        sender_email="vendor@example.com",
        sender_name="Vendor",
        subject="Акт сверки",
        status=status,
        department_id="00-000044",
        department_name="Юридический отдел",
        is_spam=False,
        spam_reason=spam_reason,
        raw_payload_json=json.dumps(
            {
                "message_id": "<resolve@example>",
                "mailbox": "info@turbo-don.ru",
                "sender_email": "vendor@example.com",
                "subject": "Акт сверки",
                "body_text": "Просим подписать акт сверки.",
                "received_at": received_at.isoformat(),
                "routing_recipient": "jurist@turbo-don.ru",
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

    def _apply_human_resolution(
        row_id: uuid.UUID,
        *,
        status: str,
        department_id: str | None = None,
        department_name: str | None = None,
        is_spam: bool | None = None,
    ) -> EmailMessageRow | None:
        row.status = status
        row.human_review = False
        if is_spam is not None:
            row.is_spam = is_spam
        return row

    repo.apply_human_resolution.side_effect = _apply_human_resolution

    session = MagicMock()
    session_factory = MagicMock()
    session_factory.return_value.__enter__.return_value = session

    with patch("agent_pochta.api.app.get_session_factory", return_value=session_factory):
        with patch("agent_pochta.api.app.EmailRepository", return_value=repo):
            yield repo, session


def test_mark_spam_ignores_llm_not_spam_reason_in_pattern(
    learning_file: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("SPAM_LEARNING_PATH", str(learning_file))
    from agent_pochta.config import reset_settings

    reset_settings()

    row = _email_row(
        status=ProcessingStatus.DONE.value,
        spam_reason="Не спам — confidence 98%",
    )
    client = TestClient(app)

    with _mock_repo(row):
        response = client.post(
            f"/api/v1/email-messages/{row.id}/resolve-human",
            json={"decision": "mark_spam"},
        )

    assert response.status_code == 200
    store = load_spam_learning(learning_file)
    assert len(store["entries"]) == 1
    assert store["entries"][0]["label"] == "spam"
    assert store["entries"][0]["reason"] == "Отмечено офис-менеджером"
