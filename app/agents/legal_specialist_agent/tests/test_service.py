from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.agents import agent_registry
from app.agents.legal_specialist_agent.config import LEGAL_SPECIALIST_AGENT_ID
from app.agents.legal_specialist_agent.decisions import add_workdays, assess_case
from app.agents.legal_specialist_agent.prompts import (
    RAG_EMPTY_DEFAULT,
    SYSTEM_PROMPT,
    build_messages,
    parse_recommendation,
)
from app.agents.legal_specialist_agent.schemas import LegalSpecialistAgentRequest
from app.services.legal_specialist_permission import is_legal_specialist_position


def _claim_context(**extra):
    ctx = {
        "upstream": {"supplier_id": "SUP-1", "contract_status": "active"},
        "open_advances": [
            {
                "amount": "15000",
                "advance_date": str(date.today() - timedelta(days=45)),
            }
        ],
        "escalation_reason_code": "CLAIM_REQUIRED",
    }
    ctx.update(extra)
    return ctx


def _base_payload(**overrides):
    payload = {
        "task_id": "task-1",
        "case_id": "case-1",
        "correlation_id": "contour4:test:case-1",
        "idempotency_key": "ls:case-1:v1",
        "case_context": _claim_context(),
    }
    payload.update(overrides)
    return payload


def test_agent_registered():
    assert agent_registry.get(LEGAL_SPECIALIST_AGENT_ID) is not None


def test_position_markers():
    assert is_legal_specialist_position("Юрист")
    assert is_legal_specialist_position("Юрисконсульт")
    assert is_legal_specialist_position("Юридическая служба")
    assert not is_legal_specialist_position("Бухгалтер")
    assert not is_legal_specialist_position(None)


@pytest.mark.asyncio
async def test_run_awaits_human_with_claim_draft():
    agent_cls = agent_registry.get(LEGAL_SPECIALIST_AGENT_ID)
    assert agent_cls is not None
    result = await agent_cls().run(_base_payload())
    assert result.role_status == "waiting_human"
    assert result.suggested_action == "approve_claim_draft"
    assert result.output_data["claim_status"] == "draft"
    assert result.output_data["claim_draft"] is not None
    assert "llm_recommendation" in result.output_data


@pytest.mark.asyncio
async def test_run_not_required_without_advances():
    agent_cls = agent_registry.get(LEGAL_SPECIALIST_AGENT_ID)
    result = await agent_cls().run(
        _base_payload(case_context=_claim_context(open_advances=[]))
    )
    assert result.role_status == "completed"
    assert result.output_data["claim_status"] == "not_required"


@pytest.mark.asyncio
async def test_run_data_check_without_supplier():
    agent_cls = agent_registry.get(LEGAL_SPECIALIST_AGENT_ID)
    result = await agent_cls().run(
        _base_payload(case_context={"open_advances": [{"amount": "1"}]})
    )
    assert result.role_status == "data_check"
    assert "upstream.supplier_id" in result.output_data["missing_fields"]


@pytest.mark.asyncio
async def test_human_approve_claim():
    agent_cls = agent_registry.get(LEGAL_SPECIALIST_AGENT_ID)
    result = await agent_cls().run(
        _base_payload(
            human_action="approve_claim_draft",
            human_payload={"claim_draft": {"subject": "x"}},
        )
    )
    assert result.role_status == "completed"
    assert result.output_data["claim_status"] == "approved"
    assert result.next_roles_suggested == []


@pytest.mark.asyncio
async def test_human_lawsuit_blocked_early():
    agent_cls = agent_registry.get(LEGAL_SPECIALIST_AGENT_ID)
    result = await agent_cls().run(
        _base_payload(
            human_action="prepare_lawsuit",
            case_context=_claim_context(
                open_advances=[
                    {
                        "amount": "20000",
                        "advance_date": str(date.today() - timedelta(days=10)),
                    }
                ]
            ),
        )
    )
    assert result.role_status == "blocked"
    assert result.output_data["claim_status"] == "lawsuit_blocked_early"


@pytest.mark.asyncio
async def test_human_lawsuit_blocked_fee():
    agent_cls = agent_registry.get(LEGAL_SPECIALIST_AGENT_ID)
    result = await agent_cls().run(
        _base_payload(
            human_action="prepare_lawsuit",
            case_context=_claim_context(
                open_advances=[
                    {
                        "amount": "1000",
                        "advance_date": str(date.today() - timedelta(days=60)),
                    }
                ]
            ),
        )
    )
    assert result.role_status == "blocked"
    assert result.output_data["claim_status"] == "lawsuit_blocked_fee"


@pytest.mark.asyncio
async def test_human_prepare_lawsuit_ok():
    agent_cls = agent_registry.get(LEGAL_SPECIALIST_AGENT_ID)
    result = await agent_cls().run(
        _base_payload(human_action="prepare_lawsuit")
    )
    assert result.role_status == "completed"
    assert result.output_data["claim_status"] == "lawsuit_pack"
    assert result.output_data["lawsuit_pack"]["status"] == "pack_ready"


@pytest.mark.asyncio
async def test_human_return():
    agent_cls = agent_registry.get(LEGAL_SPECIALIST_AGENT_ID)
    result = await agent_cls().run(
        _base_payload(human_action="return", human_payload={"comment": "правка"})
    )
    assert result.role_status == "blocked"
    assert result.output_data["claim_status"] == "returned"


@pytest.mark.asyncio
async def test_validation_failed_payload():
    agent_cls = agent_registry.get(LEGAL_SPECIALIST_AGENT_ID)
    result = await agent_cls().run({"task_id": "t1"})
    assert result.role_status == "failed"


def test_add_workdays_skips_weekend():
    # Friday + 1 workday -> Monday
    friday = date(2026, 7, 17)
    assert friday.weekday() == 4
    assert add_workdays(friday, 1) == date(2026, 7, 20)


def test_assess_builds_sla():
    request = LegalSpecialistAgentRequest.model_validate(_base_payload())
    assessment = assess_case(request)
    assert assessment.kind == "awaiting_claim"
    assert assessment.claim_sla is not None
    assert assessment.claim_sla["prepare_days"] == 2


def test_system_prompt_loaded():
    assert "legal specialist" in SYSTEM_PROMPT.lower() or "legal" in SYSTEM_PROMPT.lower()
    assert "Для редакторов" not in SYSTEM_PROMPT


def test_prompts_loaded_from_markdown_files():
    from app.agents.legal_specialist_agent.prompts import (
        get_system_prompt,
        get_user_prompt_template,
    )

    assert get_system_prompt().startswith("You are an AI assistant")
    assert "{rag}" in get_user_prompt_template()


def test_build_messages_uses_rag_default():
    request = LegalSpecialistAgentRequest.model_validate(_base_payload())
    assessment = assess_case(request)
    messages = build_messages(request, assessment, rag_text="")
    assert RAG_EMPTY_DEFAULT in messages[1]["content"]


def test_parse_recommendation_json():
    parsed = parse_recommendation(
        '{"recommendation":"Ок","rationale":"x",'
        '"suggested_action":"approve_claim_draft","needs_hitl":true,'
        '"norm_refs":["СТО-28-020 претензии"]}'
    )
    assert parsed["suggested_action"] == "approve_claim_draft"
