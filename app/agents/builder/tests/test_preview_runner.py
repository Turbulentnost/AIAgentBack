from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.agents.builder.preview_runner import build_partial_preview_message, run_agent_preview


def test_build_partial_preview_message():
    grounding = {
        "current_date": {"date_human": "9 июня 2026", "date_iso": "2026-06-09"},
        "skipped_tools": ["fetch_page_via_user_browser", "search_knowledge_base"],
        "errors": {},
    }
    text = build_partial_preview_message(grounding)
    assert "9 июня 2026" in text
    assert "Sandbox" in text
    assert "[" not in text
    assert "плейсхолдер" not in text.lower()


@pytest.mark.asyncio
async def test_run_agent_preview_partial_skips_llm():
    mock_generate = AsyncMock()
    with patch(
        "app.agents.builder.preview_runner.builder_llm.generate_preview_sample",
        new=mock_generate,
    ):
        result = await run_agent_preview(
            goal="Нужно просмотреть в браузере сайты для погоды и вывести на сегодня",
            requirements={"agent_type": "consultant"},
            blueprint={"tools": ["get_current_date", "fetch_page_via_user_browser"]},
        )

    assert result["success"] is True
    assert result["preview_type"] == "partial_grounding"
    assert "Sandbox" in result["output_text"]
    mock_generate.assert_not_called()


@pytest.mark.asyncio
async def test_run_agent_preview_uses_llm_when_substantive():
    mock_generate = AsyncMock(return_value=type("Sample", (), {"output_text": "Готовый ответ"})())
    grounding = {
        "current_date": {"date_iso": "2026-06-09"},
        "tool_results": {
            "get_current_date": {"date_iso": "2026-06-09"},
            "fetch_page_via_user_browser": {"text": "Температура +15°C, облачно"},
        },
        "skipped_tools": [],
        "errors": {},
        "has_substantive_data": True,
        "mode": "tool_execution",
    }
    with (
        patch(
            "app.agents.builder.preview_runner.collect_preview_grounding",
            new=AsyncMock(return_value=grounding),
        ),
        patch(
            "app.agents.builder.preview_runner.builder_llm.generate_preview_sample",
            new=mock_generate,
        ),
    ):
        result = await run_agent_preview(
            goal="Погода",
            requirements={},
            blueprint={"tools": ["fetch_page_via_user_browser"]},
            db=object(),
            user=object(),
        )

    assert result["success"] is True
    assert result["output_text"] == "Готовый ответ"
    mock_generate.assert_awaited_once()
