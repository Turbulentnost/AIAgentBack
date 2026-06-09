from __future__ import annotations

from app.agents.builder.llm import merge_requirements, parse_json_content


def test_parse_json_content_from_codeblock():
    data = parse_json_content('```json\n{"ready_to_plan": true, "assistant_message": "ok"}\n```')
    assert data["ready_to_plan"] is True


def test_merge_requirements_skips_empty():
    merged = merge_requirements({"inputs": "a"}, {"outputs": "", "human_approval": False})
    assert merged["inputs"] == "a"
    assert merged["human_approval"] is False
    assert "outputs" not in merged
