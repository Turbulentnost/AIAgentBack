"""Общие фикстуры тестов."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def unit_test_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unit-тесты графа — всегда на заглушках, без PostgreSQL/Qdrant/LLM."""
    monkeypatch.setenv("USE_STUBS", "true")
    monkeypatch.setenv("RAG_BACKEND", "stub")
    from agent_pochta.config import reset_settings

    reset_settings()
    monkeypatch.setattr(
        "agent_pochta.db.repository.persist_processing_result",
        lambda _state: None,
    )
    yield
    reset_settings()
