"""Tests for incremental history during SSE stream."""

from __future__ import annotations

from app.gost.catalog import GOST_LINE_KEYS
from app.services.history_stream_service import build_partial_payload, new_stream_state


def test_build_partial_payload_gost_all_passed_initially():
    state = new_stream_state()
    state["job_id"] = "job1"
    state["total_items"] = 3
    payload = build_partial_payload(state, status="running")
    assert payload["status"] == "running"
    assert payload["gost_summary"]["passed"] == GOST_LINE_KEYS
    assert payload["gost_summary"]["errors"] == {}


def test_build_partial_payload_updates_with_items():
    state = new_stream_state()
    state["job_id"] = "job1"
    state["total_items"] = 2
    state["items"] = [
        {
            "page": 1,
            "errors_count": 1,
            "warnings_count": 0,
            "errors": [{"code": "missing_signature", "gost_reference": "ГОСТ Р 2.104-2023"}],
            "warnings": [],
        }
    ]
    payload = build_partial_payload(state, status="running")
    assert payload["processed"] == 1
    assert payload["total_errors"] == 1
    assert "2.104" in payload["gost_summary"]["errors"]
