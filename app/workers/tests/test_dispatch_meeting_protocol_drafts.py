from __future__ import annotations

from unittest.mock import AsyncMock, patch

from app.workers.tasks import dispatch_meeting_protocol_drafts


def test_dispatch_meeting_protocol_drafts_skips_when_disabled() -> None:
    with patch("app.core.config.settings.MEETING_PROTOCOL_DRAFT_ENABLED", False):
        result = dispatch_meeting_protocol_drafts()
    assert result["skipped"] is True
    assert result["reason"] == "protocol_draft_disabled"


def test_dispatch_meeting_protocol_drafts_runs_dispatch() -> None:
    with (
        patch("app.core.config.settings.MEETING_PROTOCOL_DRAFT_ENABLED", True),
        patch("app.core.config.settings.MEETING_PROTOCOL_DISPATCH_BEAT_ENABLED", True),
        patch(
            "app.services.meeting_protocol_dispatch_service.run_protocol_draft_dispatch",
            new=AsyncMock(
                return_value={
                    "scheduled": 2,
                    "catchup_created": 1,
                    "skipped": 0,
                    "errors": [],
                }
            ),
        ),
    ):
        result = dispatch_meeting_protocol_drafts()

    assert result["scheduled"] == 2
    assert result["catchup_created"] == 1
    assert "finished_at" in result
