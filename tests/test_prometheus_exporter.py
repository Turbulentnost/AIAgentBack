"""Тесты Prometheus-метрик agent-pochta."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient


from agent_pochta.metrics.prometheus_exporter import (
    _change_percent,
    _count_routing_corrections_by_department,
    _department_label,
    _keep_rate,
    collect_department_distributions,
    collect_metrics_snapshot,
    metrics_snapshot_for_tests,
    refresh_prometheus_metrics,
    rolling_24h_window_utc,
)


def test_change_percent_zero_messages() -> None:
    assert _change_percent(5, 0) == 0.0


def test_change_percent_calculation() -> None:
    assert _change_percent(3, 100) == 3.0


def test_keep_rate_zero_total() -> None:
    assert _keep_rate(0, 0) == 0.0


def test_keep_rate_calculation() -> None:
    assert _keep_rate(4, 12) == 0.25


def test_department_label_trims_and_truncates() -> None:
    assert _department_label("  Отдел МТО  ") == "Отдел МТО"
    assert _department_label("") == "(пусто)"
    long = "А" * 200
    assert len(_department_label(long)) == 120
    assert _department_label(long).endswith("…")


def test_rolling_24h_window_is_24_hours() -> None:
    start, end = rolling_24h_window_utc()
    delta = end - start
    assert 23.9 <= delta.total_seconds() / 3600 <= 24.1


def test_collect_metrics_snapshot_with_mock_session() -> None:
    session = MagicMock()
    # 10 change/message counts + operator_saved + operator_changed
    session.scalar.side_effect = [10, 100, 50, 500, 2, 20, 1, 5, 3, 15, 4, 12, 20, 3]

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
    assert snapshot["agent_pochta_operator_saved_total"] == 4.0
    assert snapshot["agent_pochta_operator_changed_total"] == 12.0
    assert snapshot["agent_pochta_operator_keep_rate"] == 0.25
    assert snapshot["agent_pochta_bge_routing_total_last_24h"] == 20.0
    assert snapshot["agent_pochta_bge_routing_errors_last_24h"] == 3.0
    assert snapshot["agent_pochta_bge_routing_error_rate"] == 0.15
    assert snapshot["agent_pochta_bge_operator_keep_rate"] == 0.85


def test_count_routing_corrections_by_department(tmp_path) -> None:
    path = tmp_path / "routing_corrections.json"
    path.write_text(
        '{"version":"1.0","entries":['
        '{"department_id":"00-000065","department_name":"Отдел МТО"},'
        '{"department_id":"00-000065","department_name":"Отдел МТО"},'
        '{"department_id":"00-000128","department_name":"Отдел продаж БМИ"},'
        '{"department_id":"00-000099","department_name":""}'
        "]}",
        encoding="utf-8",
    )
    counts = _count_routing_corrections_by_department(path)
    assert counts == {"Отдел МТО": 2.0, "Отдел продаж БМИ": 1.0, "00-000099": 1.0}


def test_count_routing_corrections_missing_file(tmp_path) -> None:
    missing = tmp_path / "absent.json"
    assert _count_routing_corrections_by_department(missing) == {}


def test_collect_department_distributions_with_mock_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = MagicMock()
    routed_rows = [("Отдел МТО", 10), ("Бухгалтерия", 3)]
    session.execute.side_effect = [
        MagicMock(all=lambda: routed_rows),
    ]
    monkeypatch.setattr(
        "agent_pochta.metrics.prometheus_exporter._count_routing_corrections_by_department",
        lambda path=None: {"Отдел МТО": 4.0, "Тендерный офис": 1.0},
    )

    dists = collect_department_distributions(session=session)
    assert dists["agent_pochta_routed_by_department"] == {"Отдел МТО": 10.0, "Бухгалтерия": 3.0}
    assert dists["agent_pochta_routing_corrections_by_department"] == {
        "Отдел МТО": 4.0,
        "Тендерный офис": 1.0,
    }


def test_metrics_names_exported() -> None:
    names = metrics_snapshot_for_tests()["metrics"]
    labeled = metrics_snapshot_for_tests()["labeled_metrics"]
    assert "agent_pochta_changes_last_24h" in names
    assert "agent_pochta_operator_saved_total" in names
    assert "agent_pochta_operator_changed_total" in names
    assert "agent_pochta_operator_keep_rate" in names
    assert "agent_pochta_bge_operator_keep_rate" in names
    assert len(names) == 19
    assert "agent_pochta_routed_by_department" in labeled
    assert "agent_pochta_routing_corrections_by_department" in labeled
    assert "agent_pochta_emails_by_department" not in labeled


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
        "agent_pochta_operator_saved_total": 8.0,
        "agent_pochta_operator_changed_total": 2.0,
        "agent_pochta_operator_keep_rate": 0.8,
    }
    fake_dists = {
        "agent_pochta_routed_by_department": {"Отдел МТО": 5.0},
        "agent_pochta_routing_corrections_by_department": {"Отдел МТО": 7.0},
    }
    monkeypatch.setattr(
        "agent_pochta.metrics.prometheus_exporter.collect_metrics_snapshot",
        lambda session=None: fake_snapshot,
    )
    monkeypatch.setattr(
        "agent_pochta.metrics.prometheus_exporter.collect_department_distributions",
        lambda session=None: fake_dists,
    )
    monkeypatch.setattr(
        "agent_pochta.metrics.prometheus_exporter.get_session_factory",
        lambda: MagicMock(**{"return_value.__enter__.return_value": MagicMock(), "return_value.__exit__.return_value": None}),
    )
    # simplify: pass an explicit session so factory is unused
    result = refresh_prometheus_metrics(session=MagicMock())
    assert result["agent_pochta_changes_last_24h"] == 7.0
    assert result["agent_pochta_routed_by_department"] == {"Отдел МТО": 5.0}
    assert result["agent_pochta_routing_corrections_by_department"] == {"Отдел МТО": 7.0}


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
        "agent_pochta_operator_saved_total": 5.0,
        "agent_pochta_operator_changed_total": 1.0,
        "agent_pochta_operator_keep_rate": 0.8333,
        "agent_pochta_routed_by_department": {"Отдел МТО": 2.0},
        "agent_pochta_routing_corrections_by_department": {"Отдел МТО": 3.0},
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
    assert "agent_pochta_operator_saved_total" in response.text
    assert "text/plain" in response.headers.get("content-type", "")
