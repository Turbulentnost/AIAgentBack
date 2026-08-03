"""Tests for check run versioning and OTK users."""

from __future__ import annotations

import uuid

from app.models.check_run import EskdCheckRun
from app.services.check_version_service import compute_check_diff, summarize_diff
from app.services.user_service import TEST_OTK_USERS


def test_compute_diff_initial():
    current = EskdCheckRun(
        id=uuid.uuid4(),
        job_id="j1",
        total_errors=2,
        total_warnings=1,
        status="done",
    )
    diff = compute_check_diff(None, current)
    assert diff["initial"] is True


def test_compute_diff_errors_delta():
    previous = EskdCheckRun(
        id=uuid.uuid4(),
        job_id="j0",
        total_errors=1,
        total_warnings=0,
        status="done",
        designation="A",
    )
    current = EskdCheckRun(
        id=uuid.uuid4(),
        job_id="j1",
        total_errors=3,
        total_warnings=0,
        status="done",
        designation="B",
    )
    diff = compute_check_diff(previous, current)
    assert diff["total_errors"]["before"] == 1
    assert diff["total_errors"]["after"] == 3
    assert diff["designation"]["after"] == "B"


def test_summarize_rerun():
    summary = summarize_diff(
        "rerun",
        {"total_errors": {"before": 1, "after": 0, "label": "ошибок"}},
        version_no=2,
    )
    assert "Версия 2" in summary
    assert "1 → 0" in summary


def test_seed_users_defined():
    logins = {row["login"] for row in TEST_OTK_USERS}
    assert "otk.ivanov" in logins
    assert "otk.petrova" in logins
    assert all(row["role"] == "ESKD_OTK" for row in TEST_OTK_USERS)
