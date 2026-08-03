"""Daily auto-poll throttle for procurement orchestrator."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.services.procurement_orchestrator_service import should_throttle_auto_poll


def test_force_bypasses_throttle() -> None:
    skip, remaining = should_throttle_auto_poll(
        datetime.now(UTC).isoformat(),
        force=True,
        min_interval_seconds=86400,
    )
    assert skip is False
    assert remaining is None


def test_skips_when_last_success_within_day() -> None:
    now = datetime(2026, 7, 24, 12, 0, tzinfo=UTC)
    last = (now - timedelta(hours=3)).isoformat()
    skip, remaining = should_throttle_auto_poll(
        last,
        force=False,
        min_interval_seconds=86400,
        now=now,
    )
    assert skip is True
    assert remaining is not None
    assert remaining == 86400 - 3 * 3600


def test_allows_when_last_success_older_than_day() -> None:
    now = datetime(2026, 7, 24, 12, 0, tzinfo=UTC)
    last = (now - timedelta(hours=25)).isoformat()
    skip, remaining = should_throttle_auto_poll(
        last,
        force=False,
        min_interval_seconds=86400,
        now=now,
    )
    assert skip is False
    assert remaining is None


def test_allows_when_no_prior_success() -> None:
    skip, remaining = should_throttle_auto_poll(
        None,
        force=False,
        min_interval_seconds=86400,
    )
    assert skip is False
    assert remaining is None
