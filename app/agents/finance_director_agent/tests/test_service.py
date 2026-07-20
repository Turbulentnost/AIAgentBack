from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.agents import agent_registry
from app.agents.finance_director_agent.config import FINANCE_DIRECTOR_AGENT_ID
from app.agents.finance_director_agent.decisions import assess_case
from app.agents.finance_director_agent.prompts import (
    RAG_EMPTY_DEFAULT,
    SYSTEM_PROMPT,
    build_messages,
    parse_recommendation,
)
from app.agents.finance_director_agent.schemas import FinanceDirectorAgentRequest
from app.services.finance_director_permission import is_finance_director_position


def _base_payload(**overrides):
    payload = {
        "task_id": "task-1",
        "case_id": "case-1",
        "correlation_id": "contour4:test:case-1",
        "idempotency_key": "fd:case-1:v1",
        "trigger": "s10_exception",
        "case_context": {
            "payment_request_id": "PR-1",
            "cfo_code": "CFO-01",
            "amount": "1000.00",
            "s10_week_remaining": "5000.00",
            "escalation_reason_code": "S10_EXCEEDED",
        },
    }
    payload.update(overrides)
    return payload


def test_agent_registered():
    assert agent_registry.get(FINANCE_DIRECTOR_AGENT_ID) is not None


def test_position_markers():
    assert is_finance_director_position("Финансовый директор")
    assert is_finance_director_position("Финдиректор отдела")
    assert not is_finance_director_position("Бухгалтер")
    assert not is_finance_director_position(None)


@pytest.mark.asyncio
async def test_run_awaits_human_when_s10_ok():
    agent_cls = agent_registry.get(FINANCE_DIRECTOR_AGENT_ID)
    assert agent_cls is not None
    result = await agent_cls().run(_base_payload())
    assert result.agent_id == FINANCE_DIRECTOR_AGENT_ID
    assert result.role_status == "waiting_human"
    assert result.requires_human_review is True
    assert result.suggested_action == "allow"
    assert result.output_data["s10_ok"] is True
    assert result.output_data["amount"] == "1000.00"
    assert "llm_recommendation" in result.output_data


@pytest.mark.asyncio
async def test_run_suggests_deny_when_s10_exceeded():
    agent_cls = agent_registry.get(FINANCE_DIRECTOR_AGENT_ID)
    result = await agent_cls().run(
        _base_payload(
            case_context={
                "amount": "9000",
                "s10_week_remaining": "1000",
                "escalation_reason_code": "S10_EXCEEDED",
            }
        )
    )
    assert result.suggested_action == "deny"
    assert result.output_data["s10_ok"] is False
    assert "S10_EXCEEDED" in result.output_data["risks"]


@pytest.mark.asyncio
async def test_procurement_limit_alias_syncs_to_s10():
    agent_cls = agent_registry.get(FINANCE_DIRECTOR_AGENT_ID)
    result = await agent_cls().run(
        _base_payload(
            case_context={
                "amount": "1000",
                "procurement_limit_week_remaining": "5000",
                "escalation_reason_code": "S10_EXCEEDED",
                "upstream": {
                    "invoice_verified": True,
                    "price_match": True,
                    "sz_required": False,
                },
            }
        )
    )
    assert result.role_status == "waiting_human"
    assert result.output_data["s10_ok"] is True
    assert str(result.output_data["procurement_limit_week_remaining"]) in {
        "5000",
        "5000.00",
    }
    assert result.output_data["invoice_verified"] is True
    assert result.output_data["price_match"] is True
    assert result.output_data["sz_required"] is False


@pytest.mark.asyncio
async def test_run_data_check_when_amount_missing():
    agent_cls = agent_registry.get(FINANCE_DIRECTOR_AGENT_ID)
    result = await agent_cls().run(
        _base_payload(case_context={"s10_week_remaining": "1000"})
    )
    assert result.role_status == "data_check"
    assert "amount" in result.output_data["missing_fields"]


@pytest.mark.asyncio
async def test_human_allow_completes():
    agent_cls = agent_registry.get(FINANCE_DIRECTOR_AGENT_ID)
    result = await agent_cls().run(_base_payload(human_action="allow"))
    assert result.role_status == "completed"
    assert result.output_data["financial_decision"] == "allow"
    assert "cfo_head_agent" in result.next_roles_suggested
    assert "executive_director_agent" in result.next_roles_suggested


@pytest.mark.asyncio
async def test_human_deny_blocks():
    agent_cls = agent_registry.get(FINANCE_DIRECTOR_AGENT_ID)
    result = await agent_cls().run(
        _base_payload(human_action="deny", human_payload={"comment": "нет обоснования"})
    )
    assert result.role_status == "blocked"
    assert result.output_data["financial_decision"] == "deny"
    assert result.output_data["reject_reason"] == "нет обоснования"


@pytest.mark.asyncio
async def test_human_defer_escalates():
    agent_cls = agent_registry.get(FINANCE_DIRECTOR_AGENT_ID)
    result = await agent_cls().run(_base_payload(human_action="defer"))
    assert result.role_status == "escalated"
    assert result.output_data["financial_decision"] == "defer"


@pytest.mark.asyncio
async def test_validation_failed_payload():
    agent_cls = agent_registry.get(FINANCE_DIRECTOR_AGENT_ID)
    result = await agent_cls().run({"task_id": "t1"})
    assert result.role_status == "failed"
    assert "validation_errors" in result.output_data


def test_assess_one_off_over_limit():
    request = FinanceDirectorAgentRequest.model_validate(
        _base_payload(
            trigger="one_off_no_contract",
            case_context={
                "amount": "15000",
                "s10_week_remaining": "50000",
                "escalation_reason_code": "ONE_OFF_NO_CONTRACT",
            },
        )
    )
    assessment = assess_case(request)
    assert "ONE_OFF_OVER_LIMIT" in assessment.risks
    assert assessment.suggested_action == "deny"


def test_assess_one_off_routes_security():
    request = FinanceDirectorAgentRequest.model_validate(
        _base_payload(
            trigger="one_off_no_contract",
            case_context={
                "amount": "5000",
                "s10_week_remaining": "50000",
                "escalation_reason_code": "ONE_OFF_NO_CONTRACT",
            },
        )
    )
    assessment = assess_case(request)
    assert assessment.suggested_action == "allow"
    assert "security_service" in assessment.next_on_allow


def test_assess_defer_when_project_price_expired():
    request = FinanceDirectorAgentRequest.model_validate(
        _base_payload(
            case_context={
                "amount": "1000",
                "s10_week_remaining": "5000",
                "escalation_reason_code": "PRICE_DELTA",
                "upstream": {
                    "project_price_valid_until": str(date.today() - timedelta(days=1)),
                    "market_quotes_count": 0,
                },
            }
        )
    )
    assessment = assess_case(request)
    assert "PROJECT_PRICE_EXPIRED" in assessment.risks
    assert assessment.suggested_action == "defer"


def test_system_prompt_has_sto_norm_refs():
    assert "СТО-28-020 §6.9" in SYSTEM_PROMPT
    assert "Для редакторов" not in SYSTEM_PROMPT
    assert "SYSTEM_PROMPT" not in SYSTEM_PROMPT


def test_prompts_loaded_from_markdown_files():
    from app.agents.finance_director_agent.prompts import (
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
    request = FinanceDirectorAgentRequest.model_validate(_base_payload())
    assessment = assess_case(request)
    messages = build_messages(request, assessment, rag_text="")
    assert messages[0]["role"] == "system"
    assert RAG_EMPTY_DEFAULT in messages[1]["content"]
    assert "{{RAG" not in messages[1]["content"]


def test_parse_recommendation_json():
    parsed = parse_recommendation(
        '{"recommendation":"Ок","rationale":"s10_ok",'
        '"suggested_action":"allow","needs_hitl":true,'
        '"norm_refs":["СТО-28-020 §6.9"]}'
    )
    assert parsed["suggested_action"] == "allow"
    assert parsed["needs_hitl"] is True
    assert "СТО-28-020 §6.9" in parsed["norm_refs"]


def test_parse_recommendation_approve_maps_to_allow():
    parsed = parse_recommendation(
        '{"recommendation":"Текст","rationale":"x",'
        '"suggested_action":"approve","needs_hitl":true,"norm_refs":[]}'
    )
    assert parsed["suggested_action"] == "allow"
