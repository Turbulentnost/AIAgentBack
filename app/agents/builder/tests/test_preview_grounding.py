from __future__ import annotations

import pytest

from app.agents.builder.preview_grounding import collect_preview_grounding


@pytest.mark.asyncio
async def test_collect_preview_grounding_includes_current_date():
    grounding = await collect_preview_grounding(
        blueprint={"tools": ["get_current_date", "search_knowledge_base"]},
        requirements={"recommended_tools": ["fetch_page_via_user_browser"]},
    )
    assert grounding["current_date"]["date_iso"]
    assert "get_current_date" in grounding["tool_results"]
    assert grounding["tool_results"]["get_current_date"]["date_iso"] == grounding["current_date"]["date_iso"]
    assert grounding["has_substantive_data"] is False
    assert grounding["mode"] == "partial_grounding"


@pytest.mark.asyncio
async def test_collect_preview_grounding_skips_unsafe_tools_without_db():
    grounding = await collect_preview_grounding(
        blueprint={"tools": ["search_knowledge_base"]},
        requirements={},
        db=None,
        user=None,
    )
    assert "search_knowledge_base" not in grounding["tool_results"]
    assert "get_current_date" in grounding["tool_results"]
    assert "search_knowledge_base" in grounding["skipped_tools"]
