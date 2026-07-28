"""Tests for check response built from saved AI run."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from app.services.marking_check_cache import build_check_response_from_run


class _FakeRun:
    def __init__(self) -> None:
        self.id = uuid.uuid4()
        self.original_filename = "sample.pdf"
        self.designation = "UFG-800"
        self.raw_result = {
            "job_id": "old",
            "status": "completed",
            "total_errors": 2,
            "total_warnings": 1,
            "items": [{"page": 1, "errors": [], "warnings": []}],
        }


def test_build_check_response_from_run_marks_cached() -> None:
    run = _FakeRun()
    payload = build_check_response_from_run(filename="sample.pdf", designation=None, run=run)  # type: ignore[arg-type]
    assert payload["status"] == "from_cache"
    assert payload["check_run_id"] == str(run.id)
    assert payload["designation"] == run.designation
    assert payload["total_errors"] == 2
    assert any("сохранённой проверки" in w for w in payload["global_warnings"])
