"""Tests for partial resume of interrupted check runs."""

from __future__ import annotations

from app.services.history_service import build_resume_plan, merge_resume_payload


def test_build_resume_plan_from_partial_items():
    plan = build_resume_plan(
        {
            "total_items": 19,
            "processed": 4,
            "items": [
                {"page": 1, "errors_count": 0},
                {"page": 2, "errors_count": 0},
                {"page": 3, "errors_count": 1},
                {"page": 4, "errors_count": 0},
            ],
        },
        pages_count=19,
    )
    assert plan["total_pages"] == 19
    assert plan["completed_pages"] == [1, 2, 3, 4]
    assert plan["remaining_pages"] == list(range(5, 20))
    assert plan["pages_param"].startswith("5,")
    assert plan["can_resume"] is True


def test_build_resume_plan_zero_progress():
    plan = build_resume_plan({"total_items": 1, "processed": 0, "items": []}, pages_count=1)
    assert plan["remaining_pages"] == [1]
    assert plan["can_resume"] is True


def test_merge_resume_payload_combines_pages():
    existing = {
        "total_items": 5,
        "items": [{"page": 1, "errors_count": 0, "warnings_count": 0, "errors": [], "warnings": []}],
    }
    new_payload = {
        "status": "completed",
        "total_items": 2,
        "items": [
            {"page": 2, "errors_count": 1, "warnings_count": 0, "errors": [{"code": "x"}], "warnings": []},
            {"page": 3, "errors_count": 0, "warnings_count": 0, "errors": [], "warnings": []},
        ],
    }
    merged = merge_resume_payload(existing, new_payload)
    assert merged["total_items"] == 5
    assert merged["processed"] == 3
    assert [item["page"] for item in merged["items"]] == [1, 2, 3]
    assert merged["total_errors"] == 1
    assert merged["status"] == "completed"
    assert "interruption_reason" not in merged
