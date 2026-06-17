from __future__ import annotations

from app.agents.nd_control_agent.prompts.nd_process_uml_prompt import (
    build_process_uml_user_prompt,
    format_process_uml_llm_input,
)


def test_format_process_uml_llm_input_maps_expected_shape() -> None:
    context = {
        "process": {"name": "Управление контрактом", "goal": "Цель"},
        "actors": ["Менеджер", "Юрист"],
        "steps": [
            {"name": "Подготовить заявку", "performer": "Менеджер", "controller": None, "system_or_resource": "1С"},
            {"name": "Согласовать", "performer": "Юрист"},
        ],
        "inputs": ["Заявка"],
        "outputs": ["Контракт"],
        "systems": ["1С"],
        "forms": ["Форма заявки"],
        "dependencies": [
            {
                "relation_type": "PROCESS_USES_SYSTEM",
                "relation_type_label": "Процесс использует систему",
                "direction": "outgoing",
                "entity_type": "System",
                "entity_name": "CRM",
            }
        ],
        "related_processes": [
            {
                "name": "Согласование договора",
                "relation_type": "PROCESS_RELATED_TO_PROCESS",
                "relation_type_label": "Процесс связан с процессом",
                "direction": "outgoing",
            }
        ],
    }

    payload = format_process_uml_llm_input(context)

    assert payload["process_name"] == "Управление контрактом"
    assert payload["actors"] == ["Менеджер", "Юрист"]
    assert len(payload["steps"]) == 2
    assert payload["steps"][0]["performer"] == "Менеджер"
    assert payload["inputs"] == ["Заявка"]
    assert payload["systems"] == ["1С"]
    assert len(payload["relations"]) == 2


def test_build_process_uml_user_prompt_contains_rules_and_json() -> None:
    context = {
        "process": {"name": "Тест"},
        "actors": [],
        "steps": [{"name": "Шаг 1"}, {"name": "Шаг 2"}, {"name": "Шаг 3"}],
        "inputs": [],
        "outputs": [],
        "systems": [],
        "forms": [],
        "dependencies": [],
        "related_processes": [],
    }

    prompt = build_process_uml_user_prompt(context)

    assert "3 шаг(ов)" in prompt
    assert '"process_name": "Тест"' in prompt
    assert "ТОЛЬКО Mermaid-код" in prompt
    assert '"steps"' in prompt
