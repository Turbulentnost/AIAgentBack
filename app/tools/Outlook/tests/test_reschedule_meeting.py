from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest

from exchangelib.recurrence import Recurrence, WeeklyPattern

from app.tools.Outlook.reschedule_meeting import (
    _series_recurrence_update_fields,
    reschedule_meeting_item,
)


def test_series_recurrence_update_fields_updates_weekly_pattern() -> None:
    config = SimpleNamespace(timezone="Europe/Moscow")
    pattern = WeeklyPattern(interval=1, weekdays=["Tuesday"])
    item = SimpleNamespace(
        recurrence=Recurrence(
            pattern=pattern,
            start=datetime(2026, 7, 14).date(),
            number=3,
        )
    )

    fields = _series_recurrence_update_fields(
        item,
        new_start=datetime(2026, 7, 15, 16, 0, tzinfo=ZoneInfo("Europe/Moscow")),
        config=config,
    )

    assert fields == ["recurrence"]
    assert pattern.weekdays == ["Wednesday"]
    assert item.recurrence.boundary.start.isoformat() == "2026-07-15"


def test_reschedule_meeting_item_occurrence_scope_saves_occurrence() -> None:
    config = SimpleNamespace(timezone="Europe/Moscow")
    occurrence = SimpleNamespace(
        type="Occurrence",
        id="occ-1",
        subject="Серия",
        is_cancelled=False,
        body="",
        start=None,
        end=None,
        location="",
    )
    occurrence.save = MagicMock()

    result = reschedule_meeting_item(
        occurrence,
        config=config,
        new_start=datetime(2026, 7, 21, 17, 0, tzinfo=ZoneInfo("Europe/Moscow")),
        new_end=datetime(2026, 7, 21, 17, 30, tzinfo=ZoneInfo("Europe/Moscow")),
        reschedule_scope="occurrence",
    )

    occurrence.save.assert_called_once()
    assert result["reschedule_scope"] == "occurrence"
    assert result["target_kind"] == "series_occurrence"


def test_reschedule_meeting_item_series_scope_saves_master() -> None:
    config = SimpleNamespace(timezone="Europe/Moscow")
    master = SimpleNamespace(
        type="RecurringMaster",
        id="master-1",
        subject="Серия",
        is_cancelled=False,
        body="",
        start=None,
        end=None,
        location="",
        recurrence=None,
    )
    master.save = MagicMock()
    master.refresh = MagicMock()
    occurrence = SimpleNamespace(
        type="Occurrence",
        id="occ-1",
        subject="Серия",
        is_cancelled=False,
        recurring_master=MagicMock(return_value=master),
    )

    result = reschedule_meeting_item(
        occurrence,
        config=config,
        new_start=datetime(2026, 7, 15, 16, 0, tzinfo=ZoneInfo("Europe/Moscow")),
        new_end=datetime(2026, 7, 15, 16, 30, tzinfo=ZoneInfo("Europe/Moscow")),
        reschedule_scope="series",
    )

    master.save.assert_called_once()
    master.refresh.assert_called_once()
    assert result["reschedule_scope"] == "series"
    assert result["target_kind"] == "series_master"
