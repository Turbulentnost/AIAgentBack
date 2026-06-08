from __future__ import annotations

from app.agents.builder.llm import BuilderLLM


def test_builder_llm_configured_with_claude_or_fallback(monkeypatch):
    monkeypatch.setattr("app.agents.builder.llm.settings.OPENAI_API_KEY_CLAUDE", None)
    monkeypatch.setattr("app.agents.builder.llm.settings.AGENT_BUILDER_CLAUDE_MODEL", "claude-sonnet-4-20250514")
    monkeypatch.setattr("app.agents.builder.llm.settings.AGENT_BUILDER_FALLBACK_BASE_URL", "http://localhost:1234/v1")
    monkeypatch.setattr("app.agents.builder.llm.settings.AGENT_BUILDER_FALLBACK_MODEL", "openai/gpt-oss-120b")
    monkeypatch.setattr("app.agents.builder.llm.settings.LLM_GATEWAY_BASE_URL", "")

    llm = BuilderLLM()
    assert llm._fallback_configured() is True
    assert llm.configured is True
