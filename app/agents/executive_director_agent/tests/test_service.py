from __future__ import annotations

import pytest

from app.agents import agent_registry
from app.agents.executive_director_agent.config import EXECUTIVE_DIRECTOR_AGENT_ID
from app.agents.executive_director_agent.decisions import assess_case
from app.agents.executive_director_agent.prompts import (
    RAG_EMPTY_DEFAULT,
    SYSTEM_PROMPT,
    build_messages,
    parse_recommendation,
)
from app.agents.executive_director_agent.schemas import ExecutiveDirectorAgentRequest
from app.services.executive_director_permission import is_executive_director_position


def _base_payload(**overrides):
    payload = {
        "task_id": "task-1",
        "case_id": "case-1",
        "correlation_id": "contour4:test:case-1",
        "idempotency_key": "ed:case-1:v1",
        "payload": {"as_of_time": "11:00"},
        "case_context": {
            "registry_id": "REG-1",
            "registry_lines": [
                {
                    "payment_request_id": "PR-1",
                    "cfo_approved": True,
                    "urgency": "high",
                },
                {
                    "payment_request_id": "PR-2",
                    "cfo_approved": True,
                    "urgency": "normal",
                },
            ],
        },
    }
    payload.update(overrides)
    return payload


def test_agent_registered():
    assert agent_registry.get(EXECUTIVE_DIRECTOR_AGENT_ID) is not None


def test_position_markers():
    assert is_executive_director_position("Исполнительный директор")
    assert is_executive_director_position("Исполнительный директор по развитию")
    assert not is_executive_director_position("Бухгалтер")
    assert not is_executive_director_position(None)


@pytest.mark.asyncio
async def test_run_awaits_human_when_registry_ready():
    agent_cls = agent_registry.get(EXECUTIVE_DIRECTOR_AGENT_ID)
    assert agent_cls is not None
    result = await agent_cls().run(_base_payload())
    assert result.agent_id == EXECUTIVE_DIRECTOR_AGENT_ID
    assert result.role_status == "waiting_human"
    assert result.requires_human_review is True
    assert result.suggested_action == "approve"
    assert result.output_data["registry_resolution"] == "pending"
    assert result.output_data["missing_cfo"] == []
    assert "llm_recommendation" in result.output_data


@pytest.mark.asyncio
async def test_run_suggests_return_when_missing_cfo():
    agent_cls = agent_registry.get(EXECUTIVE_DIRECTOR_AGENT_ID)
    result = await agent_cls().run(
        _base_payload(
            case_context={
                "registry_id": "REG-1",
                "registry_lines": [
                    {"payment_request_id": "PR-1", "cfo_approved": True},
                    {"payment_request_id": "PR-2", "cfo_approved": False},
                ],
            }
        )
    )
    assert result.suggested_action == "return"
    assert "PR-2" in result.output_data["missing_cfo"]
    assert "ROUTE_EXCEPTION" in result.output_data["risks"]


@pytest.mark.asyncio
async def test_run_data_check_when_registry_missing():
    agent_cls = agent_registry.get(EXECUTIVE_DIRECTOR_AGENT_ID)
    result = await agent_cls().run(_base_payload(case_context={}))
    assert result.role_status == "data_check"
    assert "registry_id" in result.output_data["missing_fields"]
    assert "registry_lines" in result.output_data["missing_fields"]


@pytest.mark.asyncio
async def test_human_approve_completes():
    agent_cls = agent_registry.get(EXECUTIVE_DIRECTOR_AGENT_ID)
    result = await agent_cls().run(_base_payload(human_action="approve"))
    assert result.role_status == "completed"
    assert result.output_data["registry_resolution"] == "approved"
    assert "chief_accountant_agent" in result.next_roles_suggested


@pytest.mark.asyncio
async def test_human_approve_blocked_when_missing_cfo():
    agent_cls = agent_registry.get(EXECUTIVE_DIRECTOR_AGENT_ID)
    result = await agent_cls().run(
        _base_payload(
            human_action="approve",
            case_context={
                "registry_id": "REG-1",
                "registry_lines": [
                    {"payment_request_id": "PR-9", "cfo_approved": False},
                ],
            },
        )
    )
    assert result.role_status == "blocked"
    assert result.output_data["registry_resolution"] == "blocked_missing_cfo"


@pytest.mark.asyncio
async def test_human_return_blocks():
    agent_cls = agent_registry.get(EXECUTIVE_DIRECTOR_AGENT_ID)
    result = await agent_cls().run(
        _base_payload(human_action="return", human_payload={"comment": "нет ЦФО"})
    )
    assert result.role_status == "blocked"
    assert result.output_data["registry_resolution"] == "returned"
    assert result.output_data["reject_reason"] == "нет ЦФО"


@pytest.mark.asyncio
async def test_validation_failed_payload():
    agent_cls = agent_registry.get(EXECUTIVE_DIRECTOR_AGENT_ID)
    result = await agent_cls().run({"task_id": "t1"})
    assert result.role_status == "failed"
    assert "validation_errors" in result.output_data


def test_assess_deadline_risk():
    request = ExecutiveDirectorAgentRequest.model_validate(
        _base_payload(payload={"as_of_time": "13:00"})
    )
    assessment = assess_case(request)
    assert assessment.deadline_passed is True
    assert "REGISTRY_DEADLINE_MISSED" in assessment.risks
    assert assessment.suggested_action == "approve"


def test_assess_line_priorities():
    request = ExecutiveDirectorAgentRequest.model_validate(_base_payload())
    assessment = assess_case(request)
    by_id = {p["payment_request_id"]: p["priority"] for p in assessment.priorities}
    assert by_id["PR-1"] == 1
    assert by_id["PR-2"] == 2


def test_system_prompt_has_sto_norm_refs():
    assert "СТО-28-020 §6.2" in SYSTEM_PROMPT
    assert "Для редакторов" not in SYSTEM_PROMPT
    assert "SYSTEM_PROMPT" not in SYSTEM_PROMPT


def test_prompts_loaded_from_markdown_files():
    from app.agents.executive_director_agent.prompts import (
        get_system_prompt,
        get_user_prompt_template,
    )

    system = get_system_prompt()
    user_tpl = get_user_prompt_template()
    assert system.startswith("You are an AI assistant")
    assert "{rag}" in user_tpl
    assert "<case>" in user_tpl
    assert "Для редакторов" not in system
    assert "Для редакторов" not in user_tpl


def test_build_messages_uses_rag_default():
    request = ExecutiveDirectorAgentRequest.model_validate(_base_payload())
    assessment = assess_case(request)
    messages = build_messages(request, assessment, rag_text="")
    assert messages[0]["role"] == "system"
    assert RAG_EMPTY_DEFAULT in messages[1]["content"]


def test_parse_recommendation_json():
    parsed = parse_recommendation(
        '{"recommendation":"Ок","rationale":"all lines approved",'
        '"suggested_action":"approve","needs_hitl":true,'
        '"norm_refs":["СТО-28-020 §6.2"]}'
    )
    assert parsed["suggested_action"] == "approve"
    assert parsed["needs_hitl"] is True


def test_parse_recommendation_approve_registry_maps_to_approve():
    parsed = parse_recommendation(
        '{"recommendation":"Текст","rationale":"x",'
        '"suggested_action":"approve_registry","needs_hitl":true,"norm_refs":[]}'
    )
    assert parsed["suggested_action"] == "approve"
