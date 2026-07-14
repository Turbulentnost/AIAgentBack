"""Тесты Prometheus-метрик agent-pochta."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from agent_pochta.metrics.prometheus_exporter import (
    _change_percent,
    collect_metrics_snapshot,
    metrics_snapshot_for_tests,
    refresh_prometheus_metrics,
    rolling_24h_window_utc,
)


def test_change_percent_zero_messages() -> None:
    assert _change_percent(5, 0) == 0.0


def test_change_percent_calculation() -> None:
    assert _change_percent(3, 100) == 3.0


def test_rolling_24h_window_is_24_hours() -> None:
    start, end = rolling_24h_window_utc()
    delta = end - start
    assert 23.9 <= delta.total_seconds() / 3600 <= 24.1


def test_collect_metrics_snapshot_with_mock_session() -> None:
    session = MagicMock()
    session.scalar.side_effect = [10, 100, 50, 500, 2, 20, 1, 5, 3, 15, 4, 12]

    snapshot = collect_metrics_snapshot(session=session)

    assert snapshot["agent_pochta_changes_last_24h"] == 10.0
    assert snapshot["agent_pochta_messages_last_24h"] == 100.0
    assert snapshot["agent_pochta_change_percent_last_24h"] == 10.0
    assert snapshot["agent_pochta_messages_total"] == 500.0
    assert snapshot["agent_pochta_changes_total"] == 50.0
    assert snapshot["agent_pochta_department_changes_last_24h"] == 2.0
    assert snapshot["agent_pochta_department_changes_total"] == 20.0
    assert snapshot["agent_pochta_spam_mark_last_24h"] == 1.0
    assert snapshot["agent_pochta_spam_mark_total"] == 5.0
    assert snapshot["agent_pochta_not_spam_mark_last_24h"] == 3.0
    assert snapshot["agent_pochta_not_spam_mark_total"] == 15.0


def test_metrics_names_exported() -> None:
    names = metrics_snapshot_for_tests()["metrics"]
    assert "agent_pochta_changes_last_24h" in names
    assert len(names) == 11


def test_refresh_prometheus_metrics_sets_gauges(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_snapshot = {
        "agent_pochta_changes_last_24h": 7.0,
        "agent_pochta_messages_last_24h": 70.0,
        "agent_pochta_change_percent_last_24h": 10.0,
        "agent_pochta_messages_total": 700.0,
        "agent_pochta_changes_total": 70.0,
        "agent_pochta_department_changes_last_24h": 1.0,
        "agent_pochta_department_changes_total": 10.0,
        "agent_pochta_spam_mark_last_24h": 2.0,
        "agent_pochta_spam_mark_total": 20.0,
        "agent_pochta_not_spam_mark_last_24h": 3.0,
        "agent_pochta_not_spam_mark_total": 30.0,
    }
    monkeypatch.setattr(
        "agent_pochta.metrics.prometheus_exporter.collect_metrics_snapshot",
        lambda session=None: fake_snapshot,
    )
    result = refresh_prometheus_metrics()
    assert result == fake_snapshot


def test_metrics_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("prometheus_client")
    fake_snapshot = {
        "agent_pochta_changes_last_24h": 1.0,
        "agent_pochta_messages_last_24h": 10.0,
        "agent_pochta_change_percent_last_24h": 10.0,
        "agent_pochta_messages_total": 100.0,
        "agent_pochta_changes_total": 10.0,
        "agent_pochta_department_changes_last_24h": 0.0,
        "agent_pochta_department_changes_total": 0.0,
        "agent_pochta_spam_mark_last_24h": 0.0,
        "agent_pochta_spam_mark_total": 0.0,
        "agent_pochta_not_spam_mark_last_24h": 0.0,
        "agent_pochta_not_spam_mark_total": 0.0,
    }
    import importlib

    api_module = importlib.import_module("agent_pochta.api.app")
    monkeypatch.setattr(
        api_module,
        "refresh_prometheus_metrics",
        lambda session=None: fake_snapshot,
    )

    client = TestClient(api_module.app)
    response = client.get("/metrics")
    assert response.status_code == 200
    assert "agent_pochta_changes_last_24h" in response.text
    assert "text/plain" in response.headers.get("content-type", "")
