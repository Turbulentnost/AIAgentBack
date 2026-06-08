from __future__ import annotations

import app.agents.tools  # noqa: F401
from app.agents.tools.registry import agent_tool_registry


def test_core_agent_tools_are_registered() -> None:
    names = {tool.name for tool in agent_tool_registry.list()}

    assert "fetch_page_via_user_browser" in names
    assert "list_available_knowledge_bases" in names
    assert "search_knowledge_base" in names
    assert "get_knowledge_fragment" in names
    assert "get_document_text" in names
    assert agent_tool_registry.get("get_document_text").implemented is False
