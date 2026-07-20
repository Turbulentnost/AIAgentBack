from __future__ import annotations

import pytest

from app.agents import agent_registry
from app.agents.cfo_head_agent.config import CFO_HEAD_AGENT_ID
from app.agents.cfo_head_agent.decisions import assess_case
from app.agents.cfo_head_agent.prompts import (
    RAG_EMPTY_DEFAULT,
    SYSTEM_PROMPT,
    build_messages,
    parse_recommendation,
)
from app.agents.cfo_head_agent.schemas import CfoHeadAgentRequest
from app.services.cfo_head_permission import is_cfo_head_position


def _base_payload(**overrides):
    payload = {
        "task_id": "task-1",
        "case_id": "case-1",
        "correlation_id": "contour4:test:case-1",
        "idempotency_key": "cfo:case-1:v1",
        "case_context": {
            "payment_request_id": "PR-1",
            "cfo_code": "CFO-01",
            "amount": "1000.00",
            "ds_limit": "5000.00",
            "payment_mode": "prepay",
        },
    }
    payload.update(overrides)
    return payload


def test_agent_registered():
    assert agent_registry.get(CFO_HEAD_AGENT_ID) is not None


def test_position_markers():
    assert is_cfo_head_position("Руководитель ЦФО")
    assert is_cfo_head_position("Начальник ЦФО отдела")
    assert not is_cfo_head_position("Бухгалтер")
    assert not is_cfo_head_position(None)


@pytest.mark.asyncio
async def test_run_awaits_human_when_within_limit():
    agent_cls = agent_registry.get(CFO_HEAD_AGENT_ID)
    assert agent_cls is not None
    result = await agent_cls().run(_base_payload())
    assert result.agent_id == CFO_HEAD_AGENT_ID
    assert result.role_status == "waiting_human"
    assert result.requires_human_review is True
    assert result.suggested_action == "approve"
    assert result.output_data["ds_limit_ok"] is True
    assert result.output_data["amount"] == "1000.00"
    assert "llm_recommendation" in result.output_data
    # Without gateway URL code path stays primary
    assert result.suggested_action == "approve"


@pytest.mark.asyncio
async def test_run_suggests_reject_when_limit_exceeded():
    agent_cls = agent_registry.get(CFO_HEAD_AGENT_ID)
    result = await agent_cls().run(
        _base_payload(
            case_context={
                "amount": "9000",
                "ds_limit": "1000",
                "cfo_code": "CFO-01",
            }
        )
    )
    assert result.suggested_action == "reject"
    assert result.output_data["ds_limit_ok"] is False
    assert "LIMIT_EXCEEDED" in result.output_data["risks"]


@pytest.mark.asyncio
async def test_run_data_check_when_amount_missing():
    agent_cls = agent_registry.get(CFO_HEAD_AGENT_ID)
    result = await agent_cls().run(
        _base_payload(case_context={"ds_limit": "1000", "cfo_code": "X"})
    )
    assert result.role_status == "data_check"
    assert "amount" in result.output_data["missing_fields"]


@pytest.mark.asyncio
async def test_human_approve_completes():
    agent_cls = agent_registry.get(CFO_HEAD_AGENT_ID)
    result = await agent_cls().run(_base_payload(human_action="approve"))
    assert result.role_status == "completed"
    assert result.output_data["cfo_approved"] is True
    assert "finance_director_agent" in result.next_roles_suggested


@pytest.mark.asyncio
async def test_human_reject_blocks():
    agent_cls = agent_registry.get(CFO_HEAD_AGENT_ID)
    result = await agent_cls().run(
        _base_payload(human_action="reject", human_payload={"comment": "дорого"})
    )
    assert result.role_status == "blocked"
    assert result.output_data["approval_status"] == "reject"
    assert result.output_data["reject_reason"] == "дорого"


@pytest.mark.asyncio
async def test_validation_failed_payload():
    agent_cls = agent_registry.get(CFO_HEAD_AGENT_ID)
    result = await agent_cls().run({"task_id": "t1"})
    assert result.role_status == "failed"
    assert "validation_errors" in result.output_data


def test_assess_staged_single_100_percent():
    request = CfoHeadAgentRequest.model_validate(
        _base_payload(
            case_context={
                "amount": "100",
                "ds_limit": "200",
                "payment_mode": "staged",
                "payment_stages": [{"stage_pct": "100"}],
            }
        )
    )
    assessment = assess_case(request)
    assert assessment.staged_issue is True
    assert assessment.suggested_action == "return"


def test_assess_suggested_payment_date():
    request = CfoHeadAgentRequest.model_validate(
        _base_payload(
            case_context={
                "amount": "100",
                "ds_limit": "200",
                "production_need_date": "2026-08-20",
                "delivery_days": 5,
            }
        )
    )
    assessment = assess_case(request)
    assert assessment.suggested_payment_date is not None


def test_lead_time_mismatch_when_delta_over_14():
    request = CfoHeadAgentRequest.model_validate(
        _base_payload(
            case_context={
                "amount": "100",
                "ds_limit": "200",
                "production_need_date": "2026-08-20",
                "delivery_days": 30,
                "lead_time_vvz_days": 10,
                "expected_delivery_date": "2026-08-15",
            }
        )
    )
    assessment = assess_case(request)
    assert assessment.lead_time_mismatch is True
    assert "LEAD_TIME_MISMATCH" in assessment.risks


def test_lead_time_ok_when_delta_14_or_less():
    request = CfoHeadAgentRequest.model_validate(
        _base_payload(
            case_context={
                "amount": "100",
                "ds_limit": "200",
                "delivery_days": 24,
                "lead_time_vvz_days": 10,
            }
        )
    )
    assessment = assess_case(request)
    assert assessment.lead_time_mismatch is False
    assert "LEAD_TIME_MISMATCH" not in assessment.risks


def test_lead_time_mismatch_absent_without_vvz():
    request = CfoHeadAgentRequest.model_validate(
        _base_payload(
            case_context={
                "amount": "100",
                "ds_limit": "200",
                "delivery_days": 30,
            }
        )
    )
    assessment = assess_case(request)
    assert assessment.lead_time_mismatch is False
    assert "LEAD_TIME_MISMATCH" not in assessment.risks


@pytest.mark.asyncio
async def test_run_exposes_lead_time_mismatch_flag():
    agent_cls = agent_registry.get(CFO_HEAD_AGENT_ID)
    result = await agent_cls().run(
        _base_payload(
            case_context={
                "amount": "1000",
                "ds_limit": "5000",
                "delivery_days": 40,
                "lead_time_vvz_days": 5,
                "expected_delivery_date": "2026-09-01",
            }
        )
    )
    assert result.output_data["lead_time_mismatch"] is True
    assert result.output_data["expected_delivery_date"] == "2026-09-01"
    assert "LEAD_TIME_MISMATCH" in result.output_data["risks"]


def test_system_prompt_has_sto_norm_refs():
    assert "СТО-28-020 §6.2" in SYSTEM_PROMPT
    assert "Для редакторов" not in SYSTEM_PROMPT
    assert "SYSTEM_PROMPT" not in SYSTEM_PROMPT


def test_prompts_loaded_from_markdown_files():
    from app.agents.cfo_head_agent.prompts import (
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
    request = CfoHeadAgentRequest.model_validate(_base_payload())
    assessment = assess_case(request)
    messages = build_messages(request, assessment, rag_text="")
    assert messages[0]["role"] == "system"
    assert RAG_EMPTY_DEFAULT in messages[1]["content"]
    assert "{{RAG" not in messages[1]["content"]


def test_parse_recommendation_json():
    parsed = parse_recommendation(
        '{"recommendation":"Ок","rationale":"amount<=ds_limit",'
        '"suggested_action":"approve","needs_hitl":true,'
        '"norm_refs":["СТО-28-020 §6.2"]}'
    )
    assert parsed["suggested_action"] == "approve"
    assert parsed["needs_hitl"] is True
    assert "СТО-28-020 §6.2" in parsed["norm_refs"]


def test_parse_recommendation_strips_fence():
    parsed = parse_recommendation(
        '```json\n{"recommendation":"Текст","rationale":"x",'
        '"suggested_action":"return","needs_hitl":true,"norm_refs":[]}\n```'
    )
    assert parsed["suggested_action"] == "return"
