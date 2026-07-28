"""Tests for stale / interrupted check runs in history."""

from __future__ import annotations

from datetime import datetime, timezone

from app.services.history_service import INTERRUPTED_STATUS, build_interrupted_payload, build_resume_plan


def test_build_interrupted_payload_preserves_items():
    raw = {
        "status": "running",
        "processed": 4,
        "total_items": 19,
        "items": [{"page": 1}, {"page": 2}],
    }
    ts = datetime(2026, 7, 21, 6, 27, 10, tzinfo=timezone.utc)
    payload = build_interrupted_payload(raw, reason="server_restart", interrupted_at=ts)
    assert payload["status"] == INTERRUPTED_STATUS
    assert payload["interruption_reason"] == "server_restart"
    assert payload["interrupted_at"] == ts.isoformat()
    assert payload["processed"] == 4
    assert len(payload["items"]) == 2


def test_build_interrupted_payload_empty_raw():
    payload = build_interrupted_payload(None, reason="stale_timeout")
    assert payload["status"] == INTERRUPTED_STATUS
    assert payload["interruption_reason"] == "stale_timeout"
    assert "interrupted_at" in payload


def test_build_resume_plan_after_interruption():
    raw = build_interrupted_payload(
        {
            "total_items": 19,
            "processed": 4,
            "items": [{"page": n} for n in range(1, 5)],
        },
        reason="server_restart",
    )
    plan = build_resume_plan(raw, pages_count=19)
    assert plan["can_resume"] is True
    assert plan["processed_pages"] == 4
    assert plan["remaining_pages"][0] == 5
