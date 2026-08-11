"""Тесты окна точности оператора (последние N действий)."""

from __future__ import annotations

from unittest.mock import MagicMock

from agent_pochta.stats.classification_log import collect_operator_approvals_recent


def test_collect_operator_approvals_recent_counts_last_n() -> None:
    session = MagicMock()
    # newest first as ordered by created_at.desc()
    session.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = [
        ("operator_approve",),
        ("operator_change",),
        ("operator_approve",),
        ("operator_approve",),
        ("operator_change",),
    ]

    result = collect_operator_approvals_recent(session, limit=5)
    assert result == {"saved": 3, "changed": 2, "rate": 0.6}
    session.query.return_value.filter.return_value.order_by.return_value.limit.assert_called_once_with(5)


def test_collect_operator_approvals_recent_empty() -> None:
    session = MagicMock()
    session.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = []
    result = collect_operator_approvals_recent(session, limit=200)
    assert result == {"saved": 0, "changed": 0, "rate": None}
