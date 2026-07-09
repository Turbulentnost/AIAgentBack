"""Тесты антипаттернов спама и объединённой проверки check_learned_spam_decision."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from agent_pochta.rules.spam_learning import (
    check_learned_spam,
    check_learned_spam_decision,
    load_spam_learning,
    save_spam_antipattern,
    save_spam_pattern,
)
from agent_pochta.schemas import EmailMessage


@pytest.fixture
def learning_file(tmp_path: Path) -> Path:
    path = tmp_path / "spam_learning_patterns.json"
    path.write_text('{"version": "2.0", "entries": []}\n', encoding="utf-8")
    return path


def _email(**kwargs) -> EmailMessage:
    base = dict(
        message_id="<antispam@example>",
        mailbox="info@turbo-don.ru",
        sender_email="promo@spam-offers.xyz",
        subject="Вебинар по продажам",
        body_text="Приглашаем на бесплатный вебинар только сегодня.",
        received_at=datetime.now(timezone.utc),
    )
    base.update(kwargs)
    return EmailMessage(**base)


def _configure_paths(monkeypatch: pytest.MonkeyPatch, learning_file: Path) -> None:
    monkeypatch.setenv("SPAM_LEARNING_PATH", str(learning_file))
    monkeypatch.setenv("RAG_BACKEND", "stub")
    from agent_pochta.config import reset_settings

    reset_settings()


def test_save_antipattern(learning_file: Path):
    entry = save_spam_antipattern(
        message_id="<a@example>",
        sender_email="vendor@example.com",
        subject="Акт сверки",
        body="Просим подписать акт сверки за квартал.",
        reason="Деловая переписка",
        path=learning_file,
    )
    store = load_spam_learning(learning_file)
    assert len(store["entries"]) == 1
    assert entry["reason"] == "Деловая переписка"
    assert entry["label"] == "not_spam"
    assert store["entries"][0]["sender_email"] == "vendor@example.com"
    assert "body_snippet" not in store["entries"][0]


def test_antipattern_blocks_learned_spam(
    learning_file: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    _configure_paths(monkeypatch, learning_file)

    save_spam_pattern(
        message_id="<spam@example>",
        sender_email="promo@spam-offers.xyz",
        subject="Вебинар по продажам",
        body="Приглашаем на бесплатный вебинар только сегодня.",
        spam_reason="Рекламная рассылка",
        path=learning_file,
    )
    save_spam_antipattern(
        message_id="<not-spam@example>",
        sender_email="promo@spam-offers.xyz",
        subject="Вебинар по продажам",
        body="Приглашаем на бесплатный вебинар только сегодня.",
        reason="Легитимное приглашение",
        path=learning_file,
    )

    decision = check_learned_spam_decision(_email())
    assert decision is not None
    assert decision.is_spam is False
    assert decision.entry_kind == "not_spam"
    assert check_learned_spam(_email()) is None


def test_newer_spam_pattern_overrides_older_antipattern(
    learning_file: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    _configure_paths(monkeypatch, learning_file)

    store = load_spam_learning(learning_file)
    store["entries"] = [
        {
            "id": "old-not-spam",
            "created_at": "2026-01-01T00:00:00+00:00",
            "message_id": "<old@example>",
            "sender_email": "promo@spam-offers.xyz",
            "keywords": ["вебинар", "продажам"],
            "label": "not_spam",
            "reason": "Было не спам",
        },
        {
            "id": "new-spam",
            "created_at": "2026-06-01T00:00:00+00:00",
            "message_id": "<new@example>",
            "sender_email": "promo@spam-offers.xyz",
            "keywords": ["вебинар", "продажам"],
            "label": "spam",
            "reason": "Снова спам",
        },
    ]
    learning_file.write_text(
        __import__("json").dumps(store, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    decision = check_learned_spam_decision(_email())
    assert decision is not None
    assert decision.is_spam is True
    assert decision.entry_kind == "spam"
    assert "Снова спам" in (decision.spam_result.reason if decision.spam_result else "")


def test_newer_antipattern_overrides_older_spam_pattern(
    learning_file: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    _configure_paths(monkeypatch, learning_file)

    store = load_spam_learning(learning_file)
    store["entries"] = [
        {
            "id": "old-spam",
            "created_at": "2026-01-01T00:00:00+00:00",
            "message_id": "<old-spam@example>",
            "sender_email": "promo@spam-offers.xyz",
            "keywords": ["вебинар"],
            "label": "spam",
            "reason": "Старый спам",
        },
        {
            "id": "new-not-spam",
            "created_at": "2026-06-01T00:00:00+00:00",
            "message_id": "<restore@example>",
            "sender_email": "promo@spam-offers.xyz",
            "keywords": ["вебинар"],
            "label": "not_spam",
            "reason": "Восстановлено оператором",
        },
    ]
    learning_file.write_text(
        __import__("json").dumps(store, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    decision = check_learned_spam_decision(_email())
    assert decision is not None
    assert decision.is_spam is False
    assert decision.entry_kind == "not_spam"
