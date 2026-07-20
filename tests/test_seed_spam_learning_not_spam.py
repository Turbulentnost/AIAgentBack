"""Тесты seed not_spam-паттернов деловой переписки."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agent_pochta.rules.spam_learning import (
    check_learned_spam_decision,
    load_spam_learning,
)
from agent_pochta.schemas import EmailMessage

ROOT = Path(__file__).resolve().parents[1]
SEED_SCRIPT = ROOT / "scripts" / "seed_spam_learning_not_spam.py"


def _import_seed_module():
    import importlib.util

    spec = importlib.util.spec_from_file_location("seed_spam_learning_not_spam", SEED_SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def seed_mod():
    return _import_seed_module()


def test_build_seed_entries_has_not_spam_label(seed_mod):
    categories = seed_mod.load_seed_categories()
    entries = seed_mod.build_seed_entries(categories, created_at="2026-07-03T10:00:00+00:00")
    assert len(entries) == len(categories)
    for entry in entries:
        assert entry["label"] == "not_spam"
        assert entry["message_id"].startswith("seed-not-spam-")
        assert entry["sender_email"] == ""
        assert entry["keywords"]
        assert "базовое обучение" in entry["reason"].lower()


def test_merge_seed_idempotent(seed_mod, tmp_path: Path):
    learning = tmp_path / "spam_learning_patterns.json"
    learning.write_text('{"version": "2.0", "entries": []}\n', encoding="utf-8")
    categories = seed_mod.load_seed_categories()
    entries = seed_mod.build_seed_entries(categories)
    store = load_spam_learning(learning)
    store, added1, _ = seed_mod.merge_seed_entries(store, entries)
    store, added2, updated = seed_mod.merge_seed_entries(store, entries)
    assert added1 == len(entries)
    assert added2 == 0
    assert updated == 0
    assert len(store["entries"]) == len(entries)


def test_seed_overrides_older_spam_marked_business(seed_mod, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    learning = tmp_path / "spam_learning_patterns.json"
    monkeypatch.setenv("SPAM_LEARNING_PATH", str(learning))
    monkeypatch.setenv("RAG_BACKEND", "stub")
    from agent_pochta.config import reset_settings

    reset_settings()

    store = {
        "version": "2.0",
        "entries": [
            {
                "id": "old-spam-invoice",
                "created_at": "2026-01-01T00:00:00+00:00",
                "message_id": "<old@example>",
                "sender_email": "vendor@example.com",
                "keywords": ["счёт", "выставить"],
                "label": "spam",
                "reason": "Ошибочно помечен спамом",
            }
        ],
    }
    learning.write_text(json.dumps(store, ensure_ascii=False, indent=2), encoding="utf-8")

    categories = seed_mod.load_seed_categories()
    seed_entries = seed_mod.build_seed_entries(
        [c for c in categories if c["message_id"] == "seed-not-spam-finance"],
        created_at="2026-07-03T12:00:00+00:00",
    )
    current = load_spam_learning(learning)
    current, added, _ = seed_mod.merge_seed_entries(current, seed_entries)
    learning.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")

    assert added == 1
    email = EmailMessage(
        message_id="<test@example>",
        mailbox="info@turbo-don.ru",
        sender_email="vendor@example.com",
        subject="Счёт на оплату",
        body_text="Просим выставить счёт за поставку.",
        received_at=datetime.now(timezone.utc),
    )
    decision = check_learned_spam_decision(email, path=learning)
    assert decision is not None
    assert decision.is_spam is False
    assert decision.entry_kind == "not_spam"


def test_keyword_match_any_one(seed_mod, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Одна запись с несколькими keywords — достаточно одного совпадения."""
    learning = tmp_path / "spam_learning_patterns.json"
    monkeypatch.setenv("SPAM_LEARNING_PATH", str(learning))
    monkeypatch.setenv("RAG_BACKEND", "stub")
    from agent_pochta.config import reset_settings

    reset_settings()

    categories = seed_mod.load_seed_categories()
    seed_entries = seed_mod.build_seed_entries(
        [c for c in categories if c["message_id"] == "seed-not-spam-requests"],
    )
    store = {"version": "2.0", "entries": seed_entries}
    learning.write_text(json.dumps(store, ensure_ascii=False, indent=2), encoding="utf-8")

    email = EmailMessage(
        message_id="<req@example>",
        mailbox="info@turbo-don.ru",
        sender_email="client@partner.ru",
        subject="Запрос",
        body_text="Направляем запрос на поставку оборудования.",
        received_at=datetime.now(timezone.utc),
    )
    decision = check_learned_spam_decision(email, path=learning)
    assert decision is not None
    assert decision.is_spam is False
