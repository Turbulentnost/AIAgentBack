"""Тесты обучения спам-фильтра на решениях human-in-the-loop."""

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
from agent_pochta.rules.spam_learning import (
    check_learned_spam,
    find_spam_pattern_match,
    load_spam_learning,
    remove_spam_patterns_by_message_id,
    save_spam_antipattern,
    save_spam_pattern,
)
from agent_pochta.schemas import EmailMessage, ProcessingStatus


@pytest.fixture
def learning_file(tmp_path: Path) -> Path:
    path = tmp_path / "spam_learning_patterns.json"
    path.write_text('{"version": "2.0", "entries": []}\n', encoding="utf-8")
    return path


def _email(**kwargs) -> EmailMessage:
    base = dict(
        message_id="<spam-learn@example>",
        mailbox="info@turbo-don.ru",
        sender_email="promo@spam-offers.xyz",
        subject="Вебинар по продажам",
        body_text="Приглашаем на бесплатный вебинар только сегодня.",
        received_at=datetime.now(timezone.utc),
    )
    base.update(kwargs)
    return EmailMessage(**base)


def test_save_and_apply_spam_pattern(learning_file: Path):
    save_spam_pattern(
        message_id="<spam@example>",
        sender_email="promo@spam-offers.xyz",
        subject="Вебинар по продажам",
        body="Приглашаем на бесплатный вебинар только сегодня.",
        spam_reason="Рекламная рассылка",
        path=learning_file,
    )

    store = load_spam_learning(learning_file)
    assert len(store["entries"]) == 1
    assert store["entries"][0]["label"] == "spam"
    assert store["entries"][0]["reason"] == "Рекламная рассылка"
    assert "body_snippet" not in store["entries"][0]

    matched = find_spam_pattern_match(
        sender_email="promo@spam-offers.xyz",
        subject="Вебинар по продажам",
        body="Приглашаем на бесплатный вебинар.",
        path=learning_file,
    )
    assert matched is not None
    assert matched["sender_email"] == "promo@spam-offers.xyz"


def test_check_learned_spam_returns_result(learning_file: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SPAM_LEARNING_PATH", str(learning_file))
    from agent_pochta.config import reset_settings

    reset_settings()

    save_spam_pattern(
        message_id="<spam@example>",
        sender_email="promo@spam-offers.xyz",
        subject="Вебинар по продажам",
        body="Приглашаем на бесплатный вебинар только сегодня.",
        spam_reason="Рекламная рассылка",
        path=learning_file,
    )

    result = check_learned_spam(_email())
    assert result is not None
    assert result.is_spam
    assert result.rule_hit == "learned_spam_pattern"
    assert "Рекламная рассылка" in result.reason


def test_remove_spam_patterns_by_message_id(learning_file: Path):
    save_spam_pattern(
        message_id="<spam@example>",
        sender_email="promo@spam-offers.xyz",
        subject="Вебинар по продажам",
        body="Приглашаем на бесплатный вебинар только сегодня.",
        spam_reason="Рекламная рассылка",
        path=learning_file,
    )
    save_spam_pattern(
        message_id="<other@example>",
        sender_email="other@example.com",
        subject="Другое",
        body="Другое письмо",
        spam_reason="Другое",
        path=learning_file,
    )

    result = remove_spam_patterns_by_message_id("<spam@example>", path=learning_file)
    assert result["removed_count"] == 1

    store = load_spam_learning(learning_file)
    assert len(store["entries"]) == 1
    assert store["entries"][0]["message_id"] == "<other@example>"


def test_restored_from_spam_passes_when_antipattern_exists(
    learning_file: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("SPAM_LEARNING_PATH", str(learning_file))
    from agent_pochta.config import reset_settings
    from agent_pochta.nodes.n2_spam_filter import node_spam_filter
    from agent_pochta.services import build_container

    reset_settings()
    save_spam_pattern(
        message_id="<spam@example>",
        sender_email="vendor@example.com",
        subject="Акт сверки за квартал",
        body="Просим подписать акт сверки взаиморасчётов.",
        spam_reason="Ошибочно отмечено спамом",
        path=learning_file,
    )
    save_spam_antipattern(
        message_id="<restore@example>",
        sender_email="vendor@example.com",
        subject="Акт сверки за квартал",
        body="Просим подписать акт сверки взаиморасчётов.",
        reason="Восстановлено из спама",
        path=learning_file,
    )

    result = node_spam_filter(
        {
            "email": _email(
                sender_email="vendor@example.com",
                subject="Акт сверки за квартал",
                body_text="Просим подписать акт сверки взаиморасчётов.",
            ),
            "meta": {"restored_from_spam": True},
            "trace": [],
        },
        build_container(),
    )
    assert result.get("status") != ProcessingStatus.SPAM
    trace = result.get("trace") or []
    assert "restored_from_spam_skip" in trace
    assert "spam_learned" not in trace


def _email_row(*, status: str) -> EmailMessageRow:
    received_at = datetime.now(timezone.utc).replace(tzinfo=None)
    return EmailMessageRow(
        id=uuid.uuid4(),
        message_id="<resolve@example>",
        received_at=received_at,
        mailbox="info@turbo-don.ru",
        sender_email="promo@spam-offers.xyz",
        sender_name="Promo",
        subject="Вебинар по продажам",
        status=status,
        spam_reason="Рекламная рассылка",
        is_spam=status == ProcessingStatus.SPAM.value,
        raw_payload_json=json.dumps(
            {
                "message_id": "<resolve@example>",
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


def test_mark_spam_on_done_saves_spam_pattern(learning_file: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SPAM_LEARNING_PATH", str(learning_file))
    from agent_pochta.config import reset_settings

    reset_settings()

    row = _email_row(status=ProcessingStatus.DONE.value)
    row.is_spam = False
    client = TestClient(app)

    with _mock_repo(row):
        response = client.post(
            f"/api/v1/email-messages/{row.id}/resolve-human",
            json={"decision": "mark_spam"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "resolved"
    assert payload["spam_pattern_saved"] is True
    assert row.status == ProcessingStatus.SPAM.value
    assert row.is_spam is True

    store = load_spam_learning(learning_file)
    assert len(store["entries"]) == 1
    assert store["entries"][0]["sender_email"] == "promo@spam-offers.xyz"
    assert store["entries"][0]["label"] == "spam"

