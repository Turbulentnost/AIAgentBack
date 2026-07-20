from __future__ import annotations

import pytest

from app.agents import agent_registry
from app.agents.chief_accountant_agent.config import CHIEF_ACCOUNTANT_AGENT_ID
from app.agents.chief_accountant_agent.decisions import assess_case
from app.agents.chief_accountant_agent.prompts import (
    RAG_EMPTY_DEFAULT,
    SYSTEM_PROMPT,
    build_messages,
    parse_recommendation,
)
from app.agents.chief_accountant_agent.schemas import ChiefAccountantAgentRequest
from app.services.chief_accountant_permission import is_chief_accountant_position


def _ok_context(**extra):
    ctx = {
        "payment_request_id": "PR-1",
        "invoice_requisites": {"complete": True, "inn": "7701234567"},
        "approvals_chain": {"cfo_approved": True},
        "open_advances": [],
    }
    ctx.update(extra)
    return ctx


def _base_payload(**overrides):
    payload = {
        "task_id": "task-1",
        "case_id": "case-1",
        "correlation_id": "contour4:test:case-1",
        "idempotency_key": "ca:case-1:v1",
        "case_context": _ok_context(),
    }
    payload.update(overrides)
    return payload


def test_agent_registered():
    assert agent_registry.get(CHIEF_ACCOUNTANT_AGENT_ID) is not None


def test_position_markers():
    assert is_chief_accountant_position("Главный бухгалтер")
    assert is_chief_accountant_position("Главбух")
    assert not is_chief_accountant_position("Бухгалтер")
    assert not is_chief_accountant_position(None)


@pytest.mark.asyncio
async def test_run_awaits_human_when_clean():
    agent_cls = agent_registry.get(CHIEF_ACCOUNTANT_AGENT_ID)
    assert agent_cls is not None
    result = await agent_cls().run(_base_payload())
    assert result.role_status == "waiting_human"
    assert result.suggested_action == "approve"
    assert result.output_data["issues"] == []
    assert "llm_recommendation" in result.output_data


@pytest.mark.asyncio
async def test_run_suggests_return_on_open_advances():
    agent_cls = agent_registry.get(CHIEF_ACCOUNTANT_AGENT_ID)
    result = await agent_cls().run(
        _base_payload(case_context=_ok_context(open_advances=[{"id": "ADV-1"}]))
    )
    assert result.suggested_action == "return"
    assert "open_advances" in result.output_data["issues"]


@pytest.mark.asyncio
async def test_run_data_check_without_ids():
    agent_cls = agent_registry.get(CHIEF_ACCOUNTANT_AGENT_ID)
    result = await agent_cls().run(_base_payload(case_context={}))
    assert result.role_status == "data_check"
    assert "registry_id|payment_request_id" in result.output_data["missing_fields"]


@pytest.mark.asyncio
async def test_human_approve_completes():
    agent_cls = agent_registry.get(CHIEF_ACCOUNTANT_AGENT_ID)
    result = await agent_cls().run(_base_payload(human_action="approve"))
    assert result.role_status == "completed"
    assert result.output_data["accounting_opinion"] == "ok"
    assert result.output_data["fully_approved"] is True
    assert "accountant_agent" in result.next_roles_suggested


@pytest.mark.asyncio
async def test_human_approve_blocked_on_missing_cfo():
    agent_cls = agent_registry.get(CHIEF_ACCOUNTANT_AGENT_ID)
    result = await agent_cls().run(
        _base_payload(
            human_action="approve",
            case_context=_ok_context(approvals_chain={"cfo_approved": False}),
        )
    )
    assert result.role_status == "blocked"
    assert result.output_data["accounting_opinion"] == "blocked"
    assert "missing_cfo_approval" in result.output_data["issues"]


@pytest.mark.asyncio
async def test_human_return_blocks():
    agent_cls = agent_registry.get(CHIEF_ACCOUNTANT_AGENT_ID)
    result = await agent_cls().run(
        _base_payload(
            human_action="return",
            human_payload={"comment": "нет ИНН", "issues": ["missing_inn"]},
        )
    )
    assert result.role_status == "blocked"
    assert result.output_data["accounting_opinion"] == "return_with_issues"
    assert result.output_data["fully_approved"] is False
    assert "missing_inn" in result.output_data["issues"]


@pytest.mark.asyncio
async def test_validation_failed_payload():
    agent_cls = agent_registry.get(CHIEF_ACCOUNTANT_AGENT_ID)
    result = await agent_cls().run({"task_id": "t1"})
    assert result.role_status == "failed"
    assert "validation_errors" in result.output_data


def test_assess_incomplete_requisites():
    request = ChiefAccountantAgentRequest.model_validate(
        _base_payload(
            case_context={
                "payment_request_id": "PR-1",
                "invoice_requisites": {"complete": False},
                "approvals_chain": {"cfo_approved": True},
            }
        )
    )
    assessment = assess_case(request)
    assert "incomplete_requisites" in assessment.issues
    assert "missing_inn" in assessment.issues
    assert assessment.suggested_action == "return"


def test_assess_accepts_registry_id_only():
    request = ChiefAccountantAgentRequest.model_validate(
        _base_payload(
            case_context=_ok_context(
                payment_request_id=None,
                registry_id="REG-9",
            )
        )
    )
    assessment = assess_case(request)
    assert assessment.missing_fields == []
    assert assessment.registry_id == "REG-9"


def test_system_prompt_has_sto_norm_refs():
    assert "СТО-28-020 §6.2" in SYSTEM_PROMPT
    assert "Для редакторов" not in SYSTEM_PROMPT


def test_prompts_loaded_from_markdown_files():
    from app.agents.chief_accountant_agent.prompts import (
        get_system_prompt,
        get_user_prompt_template,
    )

    system = get_system_prompt()
    user_tpl = get_user_prompt_template()
    assert system.startswith("You are an AI assistant")
    assert "{rag}" in user_tpl
    assert "Для редакторов" not in system


def test_build_messages_uses_rag_default():
    request = ChiefAccountantAgentRequest.model_validate(_base_payload())
    assessment = assess_case(request)
    messages = build_messages(request, assessment, rag_text="")
    assert RAG_EMPTY_DEFAULT in messages[1]["content"]


def test_parse_recommendation_json():
    parsed = parse_recommendation(
        '{"recommendation":"Ок","rationale":"ok",'
        '"suggested_action":"approve","needs_hitl":true,'
        '"norm_refs":["СТО-28-020 §6.2"]}'
    )
    assert parsed["suggested_action"] == "approve"
    assert parsed["needs_hitl"] is True
