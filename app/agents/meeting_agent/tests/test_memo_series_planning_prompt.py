from app.agents.meeting_agent.prompts.memo_series_planning_prompt import (
    MEMO_SERIES_PLANNING_SYSTEM_PROMPT,
)


def test_memo_series_planning_prompt_includes_few_shot_examples() -> None:
    assert "Пример 1" in MEMO_SERIES_PLANNING_SYSTEM_PROMPT
    assert "2026-07-20" in MEMO_SERIES_PLANNING_SYSTEM_PROMPT
    assert "2026-07-24" in MEMO_SERIES_PLANNING_SYSTEM_PROMPT
    assert '"frequency": "weekly"' in MEMO_SERIES_PLANNING_SYSTEM_PROMPT
    assert '"is_series": false' in MEMO_SERIES_PLANNING_SYSTEM_PROMPT
