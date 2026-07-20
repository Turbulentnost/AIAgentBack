from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.agents import agent_registry
from app.agents.accountant_agent.config import ACCOUNTANT_AGENT_ID
from app.agents.accountant_agent.decisions import assess_case
from app.agents.accountant_agent.prompts import (
    RAG_EMPTY_DEFAULT,
    SYSTEM_PROMPT,
    build_messages,
    parse_recommendation,
)
from app.agents.accountant_agent.schemas import AccountantAgentRequest
from app.services.accountant_permission import is_accountant_position


def _ok_context(**extra):
    ctx = {
        "payment_request_id": "PR-1",
        "fully_approved": True,
        "payment_planned_date": str(date.today() + timedelta(days=3)),
        "payment_status": "planned",
        "amount": "1000.00",
    }
    ctx.update(extra)
    return ctx


def _base_payload(**overrides):
    payload = {
        "task_id": "task-1",
        "case_id": "case-1",
        "correlation_id": "contour4:test:case-1",
        "idempotency_key": "ac:case-1:v1",
        "case_context": _ok_context(),
    }
    payload.update(overrides)
    return payload


def test_agent_registered():
    assert agent_registry.get(ACCOUNTANT_AGENT_ID) is not None


def test_position_markers():
    assert is_accountant_position("Бухгалтер")
    assert is_accountant_position("Бухгалтер по оплатам")
    assert not is_accountant_position("Главный бухгалтер")
    assert not is_accountant_position("Главбух")
    assert not is_accountant_position(None)


@pytest.mark.asyncio
async def test_run_awaits_human_in_queue():
    agent_cls = agent_registry.get(ACCOUNTANT_AGENT_ID)
    assert agent_cls is not None
    result = await agent_cls().run(_base_payload())
    assert result.role_status == "waiting_human"
    assert result.suggested_action == "mark_paid"
    assert "llm_recommendation" in result.output_data


@pytest.mark.asyncio
async def test_run_blocked_without_fully_approved():
    agent_cls = agent_registry.get(ACCOUNTANT_AGENT_ID)
    result = await agent_cls().run(
        _base_payload(case_context=_ok_context(fully_approved=False))
    )
    assert result.role_status == "blocked"
    assert result.output_data["block_payment"] is True


@pytest.mark.asyncio
async def test_run_data_check_without_payment_id():
    agent_cls = agent_registry.get(ACCOUNTANT_AGENT_ID)
    result = await agent_cls().run(
        _base_payload(case_context={"fully_approved": True})
    )
    assert result.role_status == "data_check"
    assert "payment_request_id" in result.output_data["missing_fields"]


@pytest.mark.asyncio
async def test_run_already_paid_completes_with_notify():
    agent_cls = agent_registry.get(ACCOUNTANT_AGENT_ID)
    result = await agent_cls().run(
        _base_payload(
            case_context=_ok_context(
                payment_status="paid",
                payment_actual_date=str(date.today()),
            )
        )
    )
    assert result.role_status == "completed"
    assert result.output_data["payment_status"] == "paid"
    assert "contour5" in result.output_data["notify_contours"]
    assert result.next_roles_suggested == []


@pytest.mark.asyncio
async def test_run_overdue_suggests_escalate():
    agent_cls = agent_registry.get(ACCOUNTANT_AGENT_ID)
    result = await agent_cls().run(
        _base_payload(
            case_context=_ok_context(
                payment_planned_date=str(date.today() - timedelta(days=2)),
                payment_status="planned",
            )
        )
    )
    assert result.role_status == "waiting_human"
    assert result.suggested_action == "escalate_overdue"
    assert "PAYMENT_OVERDUE" in result.output_data["risks"]


@pytest.mark.asyncio
async def test_run_cancel_pending():
    agent_cls = agent_registry.get(ACCOUNTANT_AGENT_ID)
    result = await agent_cls().run(
        _base_payload(case_context=_ok_context(cancel_requested=True))
    )
    assert result.role_status == "waiting_human"
    assert result.suggested_action == "cancel"
    assert result.output_data["payment_status"] == "cancel_pending"


@pytest.mark.asyncio
async def test_human_mark_paid():
    agent_cls = agent_registry.get(ACCOUNTANT_AGENT_ID)
    result = await agent_cls().run(_base_payload(human_action="mark_paid"))
    assert result.role_status == "completed"
    assert result.output_data["payment_status"] == "paid"
    assert "contour5" in result.output_data["notify_contours"]
    assert result.next_roles_suggested == []


@pytest.mark.asyncio
async def test_human_defer():
    agent_cls = agent_registry.get(ACCOUNTANT_AGENT_ID)
    result = await agent_cls().run(_base_payload(human_action="defer"))
    assert result.role_status == "completed"
    assert result.output_data["payment_status"] == "deferred"


@pytest.mark.asyncio
async def test_human_cancel():
    agent_cls = agent_registry.get(ACCOUNTANT_AGENT_ID)
    result = await agent_cls().run(
        _base_payload(human_action="cancel", human_payload={"comment": "отмена"})
    )
    assert result.role_status == "completed"
    assert result.output_data["payment_status"] == "cancelled"
    assert result.output_data["reject_reason"] == "отмена"


@pytest.mark.asyncio
async def test_validation_failed_payload():
    agent_cls = agent_registry.get(ACCOUNTANT_AGENT_ID)
    result = await agent_cls().run({"task_id": "t1"})
    assert result.role_status == "failed"


def test_assess_overdue_delivery():
    from app.agents.cfo_head_agent.sto_dates import add_workdays

    need = date.today()
    request = AccountantAgentRequest.model_validate(
        _base_payload(
            case_context=_ok_context(
                payment_planned_date=str(date.today() - timedelta(days=5)),
                production_need_date=str(need),
            )
        )
    )
    assessment = assess_case(request)
    assert assessment.overdue is True
    assert assessment.delivery is not None
    # §6.6: сдвиг на рабочие дни (не календарные)
    assert assessment.delivery == add_workdays(need, assessment.delay_days).isoformat()


def test_system_prompt_has_sto_norm_refs():
    assert "СТО-28-020 §6.11" in SYSTEM_PROMPT
    assert "Для редакторов" not in SYSTEM_PROMPT


def test_prompts_loaded_from_markdown_files():
    from app.agents.accountant_agent.prompts import (
        get_system_prompt,
        get_user_prompt_template,
    )

    system = get_system_prompt()
    user_tpl = get_user_prompt_template()
    assert system.startswith("You are an AI assistant")
    assert "{rag}" in user_tpl


def test_build_messages_uses_rag_default():
    request = AccountantAgentRequest.model_validate(_base_payload())
    assessment = assess_case(request)
    messages = build_messages(request, assessment, rag_text="")
    assert RAG_EMPTY_DEFAULT in messages[1]["content"]


def test_parse_recommendation_json():
    parsed = parse_recommendation(
        '{"recommendation":"Ок","rationale":"ok",'
        '"suggested_action":"mark_paid","needs_hitl":true,'
        '"norm_refs":["СТО-28-020 §6.11"]}'
    )
    assert parsed["suggested_action"] == "mark_paid"
