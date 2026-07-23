from __future__ import annotations

from datetime import date

from app.services.meeting_memo_recurrence import text_implies_bounded_duration
from app.services.meeting_memo_series_calendar import format_series_calendar_context


def test_text_implies_bounded_duration_for_whole_week_and_quarter() -> None:
    assert text_implies_bounded_duration("ежедневно на всю неделю")
    assert text_implies_bounded_duration("еженедельно на весь квартал")
    assert not text_implies_bounded_duration("еженедельно по средам")
    assert not text_implies_bounded_duration("на две недели ежедневно")


def test_format_series_calendar_context_includes_quarter_end() -> None:
    context = format_series_calendar_context(date(2026, 7, 24))
    assert "2026-09-30" in context
    assert "2026-07-24" in context
