"""Общие фикстуры тестов."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def unit_test_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unit-тесты графа — всегда на заглушках, без PostgreSQL/Qdrant/LLM."""
    monkeypatch.setenv("USE_STUBS", "true")
    monkeypatch.setenv("RAG_BACKEND", "stub")
    monkeypatch.setenv("USE_ODATA_ERP", "false")
    monkeypatch.setenv("ERP_MODE", "stub")
    monkeypatch.setenv("ODATA_FILE_STORAGE_MODE", "database")
    monkeypatch.delenv("ODATA_FILE_VOLUME_KEY", raising=False)
    from agent_pochta.config import reset_settings
    from agent_pochta.routing.deterministic_sales import reset_deterministic_sales_rules_cache

    reset_settings()
    reset_deterministic_sales_rules_cache()
    monkeypatch.setattr(
        "agent_pochta.db.repository.persist_processing_result",
        lambda _state: None,
    )
    yield
    reset_settings()
    reset_deterministic_sales_rules_cache()
