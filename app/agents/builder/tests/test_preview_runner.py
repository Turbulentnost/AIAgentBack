from __future__ import annotations

from app.agents.builder.preview_runner import build_blueprint_summary


def _consultant_blueprint() -> dict:
    return {
        "agent_type": "consultant",
        "agent_card": {"name": "Погодный консультант", "purpose": "Показывать погоду"},
        "tools": ["get_current_date", "web_search", "fetch_page_via_user_browser"],
        "input_schema": {"type": "object", "properties": {"city": {"type": "string"}}},
        "output_schema": {"type": "object", "properties": {"forecast": {"type": "string"}}},
        "workflow_graph": {
            "nodes": [
                {"id": "start", "type": "start"},
                {"id": "step_1", "type": "step", "capability": "receive_question"},
                {"id": "step_2", "type": "step", "capability": "knowledge_search"},
                {"id": "step_3", "type": "step", "capability": "present_answer"},
                {"id": "end", "type": "end"},
            ],
            "edges": [],
        },
    }


def test_build_blueprint_summary_is_static_and_mentions_runtime_sandbox():
    summary = build_blueprint_summary(
        goal="Показывать погоду в Ростове-на-Дону на сегодня",
        requirements={"agent_type": "consultant"},
        blueprint=_consultant_blueprint(),
        validation={"valid": True, "errors": []},
    )

    assert summary["success"] is True
    assert summary["summary_type"] == "static_validation"
    assert summary["valid"] is True
    # Runtime dependencies are described, not executed.
    assert "web_search" in summary["runtime_dependencies"]
    assert "fetch_page_via_user_browser" in summary["runtime_dependencies"]
    assert summary["capabilities"] == ["receive_question", "knowledge_search", "present_answer"]
    assert summary["input_params"] == ["city"]
    assert summary["output_format"] == ["forecast"]

    text = summary["output_text"]
    assert "Runtime Sandbox" in text
    assert "не выполняет инструменты" in text
    # No fabricated data / placeholders / execution errors.
    assert "[" not in text
    assert "заблокирован" not in text.lower()
    assert "allowlist" not in text.lower()


def test_build_blueprint_summary_reflects_invalid_validation():
    summary = build_blueprint_summary(
        goal="Цель",
        requirements={"agent_type": "consultant"},
        blueprint=_consultant_blueprint(),
        validation={"valid": False, "errors": ["Отсутствует обязательная секция: output_schema"]},
    )

    assert summary["success"] is False
    assert summary["valid"] is False
    assert "Blueprint неполный" in summary["output_text"]
    assert "output_schema" in summary["output_text"]
