from __future__ import annotations

from app.agents.builder.meta_tools import BUILDER_META_TOOLS
from app.agents.builder.preview_grounding import _candidate_tool_names
from app.agents.builder.templates.consultant import resolve_knowledge_sources_from_tools


def test_candidate_tool_names_excludes_meta_tools():
    blueprint = {
        "tools": [
            "get_current_date",
            "fetch_page_via_user_browser",
            "save_agent_blueprint",
            "validate_agent_blueprint",
            "render_workflow_graph",
        ]
    }
    names = _candidate_tool_names(blueprint, {})
    assert "get_current_date" in names
    assert "fetch_page_via_user_browser" in names
    assert not (set(names) & BUILDER_META_TOOLS)


def test_resolve_knowledge_sources_excludes_meta_tools():
    catalog = [
        {"name": "search_knowledge_base", "description": "Поиск в БЗ", "implemented": True},
        {"name": "web_search", "description": "Поиск сайтов", "implemented": True},
        {"name": "save_agent_blueprint", "description": "Сохранение blueprint", "implemented": True},
        {"name": "render_workflow_graph", "description": "Граф", "implemented": True},
    ]
    sources = resolve_knowledge_sources_from_tools(catalog, "Найти погоду в Ростове")
    recommended = sources["recommended_tools"]
    assert "web_search" in recommended
    assert not (set(recommended) & BUILDER_META_TOOLS)
