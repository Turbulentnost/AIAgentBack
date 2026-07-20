from __future__ import annotations

from app.agents.procurement_agent.policy import (
    classify_procurement_action,
    evaluate_procurement_action,
    evaluate_procurement_tool,
)
from app.models.enums import ProcurementActionClass


def test_unknown_operation_requires_human() -> None:
    assert classify_procurement_action("new_unregistered_action") is ProcurementActionClass.HUMAN


def test_level_zero_allows_read_only_operation() -> None:
    decision = evaluate_procurement_action("check_coverage", autonomy_level=0)
    assert decision.allowed is True
    assert decision.action_class is ProcurementActionClass.READ


def test_level_zero_blocks_draft_operation() -> None:
    decision = evaluate_procurement_action("prepare_rfq", autonomy_level=0)
    assert decision.allowed is False
    assert decision.action_class is ProcurementActionClass.DRAFT


def test_forbidden_operation_remains_forbidden() -> None:
    decision = evaluate_procurement_action("execute_payment", autonomy_level=2)
    assert decision.allowed is False
    assert decision.requires_human is False
    assert decision.action_class is ProcurementActionClass.FORBIDDEN


def test_level_zero_blocks_write_tool_outside_allowlist() -> None:
    decision = evaluate_procurement_tool("create_service_memo", autonomy_level=0)
    assert decision.allowed is False
    assert decision.action_class is ProcurementActionClass.FORBIDDEN
