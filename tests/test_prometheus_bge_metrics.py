"""Tests for BGE Prometheus metrics."""

from __future__ import annotations

from agent_pochta.metrics.prometheus_exporter import (
    _bge_error_rate,
    _read_bge_holdout_accuracy,
)


def test_bge_error_rate() -> None:
    assert _bge_error_rate(0, 0) == 0.0
    assert _bge_error_rate(100, 10) == 0.1


def test_read_bge_holdout_accuracy(tmp_path, monkeypatch) -> None:
    stats_dir = tmp_path / "data" / "stats"
    stats_dir.mkdir(parents=True)
    path = stats_dir / "bge_holdout_eval.json"
    path.write_text('{"accuracy": 0.875}', encoding="utf-8")
    monkeypatch.setattr(
        "agent_pochta.metrics.prometheus_exporter.PROJECT_ROOT",
        tmp_path,
    )
    assert _read_bge_holdout_accuracy() == 0.875
