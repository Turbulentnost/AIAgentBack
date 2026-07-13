from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.tools.Outlook.meeting_series import (
    available_reschedule_scopes,
    meeting_series_fields,
    resolve_reschedule_target,
    resolve_series_target,
)


def test_resolve_series_target_reschedule_series_from_occurrence() -> None:
    master = SimpleNamespace(type="RecurringMaster", id="master-1", is_cancelled=False)
    master.refresh = MagicMock()
    occurrence = SimpleNamespace(
        type="Occurrence",
        id="occ-1",
        is_cancelled=False,
        recurring_master=MagicMock(return_value=master),
    )

    target, kind, scope = resolve_reschedule_target(occurrence, scope="series")

    assert target is master
    master.refresh.assert_called_once()
    assert kind == "series_master"
    assert scope == "series"


def test_resolve_reschedule_target_rejects_series_master_for_occurrence_scope() -> None:
    item = SimpleNamespace(type="RecurringMaster", id="master-1", is_cancelled=False)
    with pytest.raises(RuntimeError, match="Найдена серия целиком"):
        resolve_reschedule_target(item, scope="occurrence")


def test_meeting_series_fields_includes_reschedule_scope_options() -> None:
    item = SimpleNamespace(type="Occurrence", id="occ-1", recurring_master=MagicMock())
    fields = meeting_series_fields(item)
    assert fields["reschedule_scope_options"] == ["occurrence", "series"]
    assert available_reschedule_scopes(item) == ["occurrence", "series"]


def test_resolve_series_target_uses_action_label_in_error() -> None:
    item = SimpleNamespace(type="Single", id="single-1", is_cancelled=False)
    with pytest.raises(RuntimeError, match="cancel_scope=occurrence"):
        resolve_series_target(item, scope="series", action="cancel")

    with pytest.raises(RuntimeError, match="reschedule_scope=occurrence"):
        resolve_series_target(item, scope="series", action="reschedule")
