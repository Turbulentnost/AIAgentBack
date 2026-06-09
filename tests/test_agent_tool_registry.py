from __future__ import annotations

import app.tools  # noqa: F401
from app.tools.registry import tool_registry


def test_core_agent_tools_are_registered() -> None:
    names = {tool.name for tool in tool_registry.list()}

    assert "fetch_page_via_user_browser" in names
    assert "web_search" in names
    assert "list_available_knowledge_bases" in names
    assert "search_knowledge_base" in names
    assert "get_knowledge_fragment" in names
    assert "get_document_text" in names
    assert tool_registry.get("get_document_text").implemented is False
