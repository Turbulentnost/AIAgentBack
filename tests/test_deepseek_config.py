"""Настройки DeepSeek API."""

from __future__ import annotations

from agent_pochta.config import Settings, reset_settings


def test_deepseek_provider_from_api_key(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "auto")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.delenv("GIGACHAT_API_PERS", raising=False)
    reset_settings()
    settings = Settings()
    assert settings.effective_llm_provider == "deepseek"
    assert settings.effective_llm_base_url == "https://api.deepseek.com/v1"
    assert settings.effective_llm_api_key == "sk-test"
    assert settings.llm_configured is True


def test_explicit_deepseek_provider(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setenv("LLM_DEFAULT_MODEL", "deepseek-chat")
    reset_settings()
    settings = Settings()
    assert settings.effective_llm_provider == "deepseek"
    assert settings.llm_default_model == "deepseek-chat"
