from __future__ import annotations

from app.agents.nd_control_agent.prompts.nd_process_uml_prompt import (
    build_process_uml_user_prompt,
    format_process_uml_llm_input,
)


def test_format_process_uml_llm_input_maps_expected_shape() -> None:
    context = {
        "process_graph": {
            "process_name": "Управление контрактом",
            "process_goal": "Цель",
            "roles": ["Менеджер", "Юрист"],
            "actions": [
                {
                    "id": "a1",
                    "title": "Подготовить заявку",
                    "responsible_role": "Менеджер",
                    "block_type": "operation",
                },
                {
                    "id": "a2",
                    "title": "Согласовано?",
                    "responsible_role": "Юрист",
                    "block_type": "decision",
                },
            ],
            "inputs": ["Заявка"],
            "outputs": ["Контракт"],
            "systems": ["1С"],
            "forms": ["Форма заявки"],
        }
    }

    payload = format_process_uml_llm_input(context)

    assert payload["process_name"] == "Управление контрактом"
    assert payload["roles"] == ["Менеджер", "Юрист"]
    assert len(payload["actions"]) == 2
    assert payload["actions"][0]["responsible_role"] == "Менеджер"
    assert payload["standard_profile"] == "STO-34-003_GOST-19.701-90"


def test_format_process_uml_llm_input_legacy_context() -> None:
    context = {
        "process": {"name": "Тест", "goal": "Цель", "owner": "Владелец"},
        "roles": ["Менеджер"],
        "actions": [{"id": "a1", "title": "Шаг 1", "block_type": "operation"}],
        "inputs": ["Заявка"],
        "outputs": ["Контракт"],
        "systems": ["1С"],
        "forms": ["Форма"],
        "conditions": ["Согласовано?"],
        "subprocesses": [{"name": "Соседний процесс"}],
        "standard_profile": "STO-34-003_GOST-19.701-90",
    }

    payload = format_process_uml_llm_input(context)

    assert payload["process_name"] == "Тест"
    assert payload["process_goal"] == "Цель"
    assert payload["process_owner"] == "Владелец"
    assert payload["actions"][0]["title"] == "Шаг 1"
    assert payload["conditions"] == ["Согласовано?"]


def test_build_process_uml_user_prompt_contains_sto_rules_and_json() -> None:
    context = {
        "process_graph": {
            "process_name": "Тест",
            "roles": [],
            "actions": [
                {"id": "a1", "title": "Шаг 1", "block_type": "operation"},
                {"id": "a2", "title": "Шаг 2", "block_type": "operation"},
                {"id": "a3", "title": "Шаг 3", "block_type": "operation"},
            ],
            "inputs": [],
            "outputs": [],
            "systems": [],
            "forms": [],
        }
    }

    prompt = build_process_uml_user_prompt(context)

    assert "3 action(s)" in prompt
    assert "СТО-34-003" in prompt
    assert '"process_name": "Тест"' in prompt
    assert "ТОЛЬКО Mermaid-код" in prompt
    assert '"process_graph"' in prompt
