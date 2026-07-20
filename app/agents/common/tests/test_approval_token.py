"""ApprovalToken write gate — deny without valid token (ТЗ v1.5 §9.4 stub)."""

from __future__ import annotations

import pytest

from app.agents.common.approval_token import (
    ApprovalTokenError,
    assert_write_allowed,
    deny_write_without_token,
    issue_approval_token,
    parse_approval_token,
    validate_approval_token,
)


def test_deny_write_without_token():
    out = deny_write_without_token(
        {},
        case_id="case-1",
        role="accountant_agent",
        decision="mark_paid",
        scope="payment_request",
        correlation_id="contour4:test:case-1",
        task_id="task-1",
        target_id="PR-1",
    )
    assert out["ok"] is False
    assert "approval_token" in (out.get("error") or "")


def test_assert_write_allowed_raises_without_token():
    with pytest.raises(ApprovalTokenError, match="отсутствует"):
        assert_write_allowed(
            None,
            case_id="case-1",
            role="cfo_head_agent",
            decision="approve",
            scope="payment_request",
            correlation_id="corr-1",
            task_id="t-1",
        )


def test_valid_token_allows_write_helper():
    token = issue_approval_token(
        case_id="corr-1",
        role="accountant_agent",
        decision="mark_paid",
        scope="payment_request",
        correlation_id="corr-1",
        task_id="task-1",
        target_id="PR-1",
    )
    ok, err = validate_approval_token(
        token,
        case_id="corr-1",
        role="accountant_agent",
        decision="mark_paid",
        scope="payment_request",
        correlation_id="corr-1",
        task_id="task-1",
        target_id="PR-1",
    )
    assert ok is True
    assert err is None

    out = deny_write_without_token(
        {"approval_token": token.model_dump(mode="json")},
        case_id="corr-1",
        role="accountant_agent",
        decision="mark_paid",
        scope="payment_request",
        correlation_id="corr-1",
        task_id="task-1",
        target_id="PR-1",
    )
    assert out["ok"] is True


def test_parse_invalid_token_returns_none():
    assert parse_approval_token({"token_id": "x"}) is None
    assert parse_approval_token("not-a-dict") is None
