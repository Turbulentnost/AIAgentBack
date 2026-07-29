"""Qwen strategy agent: deterministic fallback + mocked LLM JSON."""

from __future__ import annotations

from datetime import date

import pytest

from app.agents.procurement_manager_agent import strategy_agent
from app.agents.procurement_manager_agent.strategy_agent import (
    deterministic_explain,
    deterministic_plan_waves,
    plan_waves,
    propose_supplier_policy,
)
from app.agents.procurement_manager_agent.tests.test_allocation import _case


def test_deterministic_plan_waves_fallback() -> None:
    today = date(2026, 7, 24)
    early = _case(
        "early",
        required="2026-07-26T00:00:00",
        lines=[("l1", "steel", "10", "2026-07-26T00:00:00")],
    )
    late = _case(
        "late",
        required="2026-09-10T00:00:00",
        lines=[("l2", "steel", "10", "2026-09-10T00:00:00")],
    )
    plan = deterministic_plan_waves([late, early], today=today)
    assert plan["source"] == "deterministic_fallback"
    waves = plan["waves"]
    assert waves
    assert any(w["label"] == "critical" for w in waves)
    assert any(w.get("mode") == "economy" for w in waves)


@pytest.mark.asyncio
async def test_plan_waves_uses_mocked_qwen(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROCUREMENT_STRATEGY_USE_QWEN", "true")

    async def fake_chat_json(*_args, **_kwargs):
        return {
            "waves": [
                {
                    "wave_id": "w1",
                    "label": "critical",
                    "mode": "urgent",
                    "case_ids": ["early"],
                    "reason": "mock",
                },
                {
                    "wave_id": "w2",
                    "label": "late",
                    "mode": "economy",
                    "case_ids": ["late"],
                    "reason": "mock late",
                },
            ],
            "rationale": "mocked qwen",
        }

    monkeypatch.setattr(strategy_agent, "_chat_json", fake_chat_json)
    early = _case(
        "early",
        required="2026-07-26T00:00:00",
        lines=[("l1", "steel", "10", "2026-07-26T00:00:00")],
    )
    late = _case(
        "late",
        required="2026-09-10T00:00:00",
        lines=[("l2", "steel", "10", "2026-09-10T00:00:00")],
    )
    plan = await plan_waves([early, late], today=date(2026, 7, 24))
    assert plan.get("source") == "qwen"
    assert plan.get("rationale") == "mocked qwen"
    assert any(w["wave_id"] == "w1" for w in plan["waves"])


@pytest.mark.asyncio
async def test_propose_policy_falls_back_when_qwen_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PROCUREMENT_STRATEGY_USE_QWEN", "false")
    early = _case(
        "early",
        required="2026-07-26T00:00:00",
        lines=[("l1", "steel", "10", "2026-07-26T00:00:00")],
    )
    queue_plan = {
        "assignments": {"early:l1": [{"supplier_id": "s1", "quantity": "10"}]},
        "lines": [
            {
                "case_id": "early",
                "line_id": "l1",
                "nomenclature_id": "steel",
                "supplier_parts": [{"supplier_id": "s1"}],
            }
        ],
        "supplier_diversity": [],
        "waves": {"waves": []},
    }
    policy = await propose_supplier_policy(
        queue_plan,
        allocation={},
        web_candidates=[],
    )
    assert policy.get("source") == "deterministic_fallback"
    assert "shortlist_supplier_ids" in policy
    assert "assignments" in policy


def test_deterministic_explain_has_summary() -> None:
    explain = deterministic_explain(
        {"policy_text": "ok", "assignments": [], "supplier_diversity": []},
        waves={"waves": [{"wave_id": "w1", "label": "critical", "mode": "urgent"}]},
    )
    assert explain.get("summary")
    assert isinstance(explain.get("tradeoffs"), list)
