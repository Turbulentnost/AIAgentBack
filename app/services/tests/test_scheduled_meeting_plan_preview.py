from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from app.models.enums import (
    ScheduledMeetingFrequency,
    ScheduledMeetingStatus,
    ScheduledMeetingType,
    ScheduledMeetingWeekday,
)
from app.schemas.scheduled_meeting import (
    ScheduledMeetingPlanConflictRead,
    ScheduledMeetingPlanPreviewRequest,
)
from app.services.scheduled_meeting_occurrences import SeriesOccurrence
from app.services.scheduled_meeting_plan_costs import (
    build_conflict_options,
    cost_reschedule_blockers,
    cost_shift_ours,
)
from app.services.scheduled_meeting_plan_preview import (
    build_plan_preview,
    evaluate_occurrence_preview,
    week_latest_allowed,
    work_week_friday,
)
from app.services.scheduled_meeting_service import ScheduledMeetingService


TZ = ZoneInfo("Europe/Moscow")


def _config() -> SimpleNamespace:
    return SimpleNamespace(timezone="Europe/Moscow")


def _occurrence(
    day: date,
    *,
    hour: int = 9,
    duration_minutes: int = 60,
) -> SeriesOccurrence:
    start = datetime.combine(day, time(hour, 0), tzinfo=TZ)
    return SeriesOccurrence(
        occurrence_date=day,
        slot_start=start,
        slot_end=start + timedelta(minutes=duration_minutes),
        outlook_item_id=None,
        outlook_changekey=None,
        subject="Проектная серия",
        is_cancelled=False,
        source="rule",
    )


def _meeting(**kwargs) -> SimpleNamespace:
    defaults = {
        "id": uuid.uuid4(),
        "title": "Проектная серия",
        "status": ScheduledMeetingStatus.CREATED,
        "outlook_series_id": None,
        "time_local": time(9, 0),
        "duration_minutes": 60,
        "frequency": ScheduledMeetingFrequency.WEEKLY,
        "interval": 1,
        "monthly_mode": None,
        "day_of_month": None,
        "weekday": ScheduledMeetingWeekday.WEDNESDAY,
        "weekday_position": None,
        "series_start_date": date(2026, 7, 15),
        "series_end_date": date(2026, 8, 12),
        "meeting_type": ScheduledMeetingType.PLANNED,
        "participants": [],
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _busy_wed() -> list[tuple[datetime, datetime]]:
    return [
        (
            datetime(2026, 7, 15, 9, 0, tzinfo=TZ),
            datetime(2026, 7, 15, 10, 0, tzinfo=TZ),
        )
    ]


def _fake_event(*, subject: str, busy_type: str = "Busy") -> SimpleNamespace:
    return SimpleNamespace(
        subject=subject,
        busy_type=busy_type,
        start=datetime(2026, 7, 15, 9, 0, tzinfo=TZ),
        end=datetime(2026, 7, 15, 10, 0, tzinfo=TZ),
    )


def test_week_latest_allowed_is_friday_work_end() -> None:
    latest = week_latest_allowed(date(2026, 7, 15), timezone_name="Europe/Moscow")
    assert latest.date() == work_week_friday(date(2026, 7, 15))
    assert latest.hour == 17
    assert latest.minute == 0


def test_evaluate_ok_when_free() -> None:
    occurrence = _occurrence(date(2026, 7, 15))
    result = evaluate_occurrence_preview(
        occurrence=occurrence,
        conflict_policy="soft_week",
        attendees=["a@turbo-don.ru"],
        busy_by_attendee={"a@turbo-don.ru": []},
        config=_config(),
        anchor_weekday=ScheduledMeetingWeekday.WEDNESDAY,
    )
    assert result.status == "ok"
    assert result.suggested_start is None
    assert result.options == []
    assert result.recommended_option is None


@patch("app.services.scheduled_meeting_plan_preview.find_quorum_slots")
def test_evaluate_lunch_break_is_rule_conflict(mock_find_quorum) -> None:
    """Слот 12:00–12:20 пересекает обед 12:00–13:00 даже при свободных календарях."""
    occurrence = _occurrence(date(2026, 7, 30), hour=12, duration_minutes=20)
    mock_find_quorum.return_value = {
        "candidates": [
            {
                "slot_start": "2026-07-30T11:00:00+03:00",
                "slot_end": "2026-07-30T11:20:00+03:00",
                "coverage": {"ratio": 1.0, "required_ok": True},
                "busy_attendees": [],
            }
        ]
    }
    result = evaluate_occurrence_preview(
        occurrence=occurrence,
        conflict_policy="soft_week",
        attendees=["a@turbo-don.ru"],
        busy_by_attendee={"a@turbo-don.ru": []},
        config=_config(),
        anchor_weekday=ScheduledMeetingWeekday.THURSDAY,
    )
    assert result.status == "shifted"
    assert any(
        item.source == "rule" and "Обеденный перерыв" in (item.event_subject or "")
        for item in result.conflicts
    )
    assert result.suggested_start == "2026-07-30 11:00"
    assert result.recommended_option == "shift_ours"


def test_evaluate_strict_keeps_conflict() -> None:
    occurrence = _occurrence(date(2026, 7, 15))
    result = evaluate_occurrence_preview(
        occurrence=occurrence,
        conflict_policy="strict",
        attendees=["a@turbo-don.ru"],
        busy_by_attendee={"a@turbo-don.ru": _busy_wed()},
        config=_config(),
    )
    assert result.status == "conflict"
    assert result.busy_attendees == ["a@turbo-don.ru"]
    assert result.suggested_start is None
    assert result.recommended_option == "keep_conflict"


def test_evaluate_skip_marks_skip() -> None:
    occurrence = _occurrence(date(2026, 7, 15))
    result = evaluate_occurrence_preview(
        occurrence=occurrence,
        conflict_policy="skip",
        attendees=["a@turbo-don.ru"],
        busy_by_attendee={"a@turbo-don.ru": _busy_wed()},
        config=_config(),
    )
    assert result.status == "skip"
    assert result.suggested_start is None
    assert result.recommended_option == "skip"


@patch("app.services.scheduled_meeting_plan_preview.attach_reschedule_hints", side_effect=lambda records, **kwargs: records)
@patch("app.services.scheduled_meeting_plan_preview.find_quorum_slots")
def test_evaluate_soft_week_shifts_to_thursday(mock_find_quorum, _mock_hints) -> None:
    occurrence = _occurrence(date(2026, 7, 15))
    mock_find_quorum.return_value = {
        "candidates": [
            {
                "slot_start": "2026-07-16T10:00:00+03:00",
                "slot_end": "2026-07-16T11:00:00+03:00",
                "coverage": {"ratio": 1.0, "required_ok": True},
                "busy_attendees": [],
            }
        ]
    }
    result = evaluate_occurrence_preview(
        occurrence=occurrence,
        conflict_policy="soft_week",
        attendees=["a@turbo-don.ru"],
        busy_by_attendee={"a@turbo-don.ru": _busy_wed()},
        config=_config(),
        anchor_weekday=ScheduledMeetingWeekday.WEDNESDAY,
    )
    assert result.status == "shifted"
    assert result.suggested_start == "2026-07-16 10:00"
    assert result.suggested_end == "2026-07-16 11:00"
    assert result.recommended_option == "shift_ours"
    shift = next(item for item in result.options if item.kind == "shift_ours")
    assert shift.available is True
    assert shift.recommended is True


@patch("app.services.scheduled_meeting_plan_preview.attach_reschedule_hints", side_effect=lambda records, **kwargs: records)
@patch("app.services.scheduled_meeting_plan_preview.find_quorum_slots")
def test_evaluate_soft_week_unresolved_when_week_busy(mock_find_quorum, _mock_hints) -> None:
    occurrence = _occurrence(date(2026, 7, 15))
    mock_find_quorum.return_value = {"candidates": []}
    result = evaluate_occurrence_preview(
        occurrence=occurrence,
        conflict_policy="soft_week",
        attendees=["a@turbo-don.ru"],
        busy_by_attendee={"a@turbo-don.ru": _busy_wed()},
        config=_config(),
    )
    assert result.status == "unresolved"
    assert result.suggested_start is None
    assert result.recommended_option == "keep_conflict"


def test_cost_shift_ours_penalizes_day_and_anchor_weekday() -> None:
    planned = datetime(2026, 7, 15, 9, 0, tzinfo=TZ)
    suggested = datetime(2026, 7, 16, 10, 0, tzinfo=TZ)
    cost = cost_shift_ours(
        planned_start=planned,
        suggested_start=suggested,
        anchor_weekday=ScheduledMeetingWeekday.WEDNESDAY,
    )
    # 1.0 base + 0.5 day + 0.25*1.0 hour + 1.0 off-anchor = 2.75
    assert cost == 2.75


def test_cost_reschedule_blockers_prefers_high_movability() -> None:
    blockers = [
        ScheduledMeetingPlanConflictRead(
            attendee_email="a@turbo-don.ru",
            event_start="2026-07-15T09:00:00+03:00",
            event_end="2026-07-15T10:00:00+03:00",
            event_subject="1:1",
            busy_type="Tentative",
            movability="high",
            reschedule_hint_start="2026-07-15T11:00:00+03:00",
            reschedule_hint_end="2026-07-15T12:00:00+03:00",
        )
    ]
    assert cost_reschedule_blockers(blockers) == 0.5


def test_build_options_recommends_reschedule_when_tentative_and_no_shift() -> None:
    planned = datetime(2026, 7, 15, 9, 0, tzinfo=TZ)
    conflicts = [
        ScheduledMeetingPlanConflictRead(
            attendee_email="a@turbo-don.ru",
            event_start="2026-07-15T09:00:00+03:00",
            event_end="2026-07-15T10:00:00+03:00",
            event_subject="Синк",
            busy_type="Tentative",
            movability="high",
            reschedule_hint_start="2026-07-15T11:00:00+03:00",
            reschedule_hint_end="2026-07-15T12:00:00+03:00",
        )
    ]
    options, recommended = build_conflict_options(
        policy="soft_week",
        planned_start=planned,
        suggested_slot=None,
        conflicts=conflicts,
        anchor_weekday=ScheduledMeetingWeekday.WEDNESDAY,
        format_slot=lambda dt: dt.strftime("%Y-%m-%d %H:%M"),
    )
    assert recommended == "reschedule_blockers"
    by_kind = {item.kind: item for item in options}
    assert by_kind["reschedule_blockers"].available is True
    assert by_kind["reschedule_blockers"].difficulty == "easy"
    assert by_kind["shift_ours"].available is False


def test_build_options_recommends_shift_when_committee_blocker() -> None:
    planned = datetime(2026, 7, 15, 9, 0, tzinfo=TZ)
    # Тот же день, +1ч — дешевле, чем переносить low-movability «комитет».
    suggested = (
        datetime(2026, 7, 15, 10, 0, tzinfo=TZ),
        datetime(2026, 7, 15, 11, 0, tzinfo=TZ),
    )
    conflicts = [
        ScheduledMeetingPlanConflictRead(
            attendee_email="a@turbo-don.ru",
            event_start="2026-07-15T09:00:00+03:00",
            event_end="2026-07-15T10:00:00+03:00",
            event_subject="Комитет по рискам",
            busy_type="Busy",
            movability="low",
            reschedule_hint_start="2026-07-15T14:00:00+03:00",
            reschedule_hint_end="2026-07-15T15:00:00+03:00",
        )
    ]
    options, recommended = build_conflict_options(
        policy="soft_week",
        planned_start=planned,
        suggested_slot=suggested,
        conflicts=conflicts,
        anchor_weekday=ScheduledMeetingWeekday.WEDNESDAY,
        format_slot=lambda dt: dt.strftime("%Y-%m-%d %H:%M"),
    )
    assert recommended == "shift_ours"
    by_kind = {item.kind: item for item in options}
    assert by_kind["shift_ours"].recommended is True
    assert by_kind["reschedule_blockers"].available is True
    assert by_kind["reschedule_blockers"].cost == 2.5
    assert by_kind["shift_ours"].cost is not None
    assert by_kind["shift_ours"].cost < by_kind["reschedule_blockers"].cost


def test_build_options_both_unavailable_recommends_keep() -> None:
    planned = datetime(2026, 7, 15, 9, 0, tzinfo=TZ)
    conflicts = [
        ScheduledMeetingPlanConflictRead(
            attendee_email="a@turbo-don.ru",
            event_start="2026-07-15T09:00:00+03:00",
            event_end="2026-07-15T10:00:00+03:00",
            event_subject=None,
            busy_type="Busy",
            movability="medium",
            source="interval",
        )
    ]
    options, recommended = build_conflict_options(
        policy="soft_week",
        planned_start=planned,
        suggested_slot=None,
        conflicts=conflicts,
        anchor_weekday=ScheduledMeetingWeekday.WEDNESDAY,
        format_slot=lambda dt: dt.strftime("%Y-%m-%d %H:%M"),
    )
    assert recommended == "keep_conflict"
    by_kind = {item.kind: item for item in options}
    assert by_kind["shift_ours"].available is False
    assert by_kind["reschedule_blockers"].available is False
    assert by_kind["keep_conflict"].recommended is True
    assert by_kind["skip"].available is True


@patch("app.services.scheduled_meeting_plan_preview.attach_reschedule_hints")
@patch("app.services.scheduled_meeting_plan_preview.find_quorum_slots")
def test_evaluate_recommends_reschedule_blockers_for_tentative(
    mock_find_quorum,
    mock_hints,
) -> None:
    occurrence = _occurrence(date(2026, 7, 15))
    mock_find_quorum.return_value = {"candidates": []}
    mock_hints.return_value = [
        {
            "event_start": "2026-07-15T09:00:00+03:00",
            "event_end": "2026-07-15T10:00:00+03:00",
            "event_subject": "Синк",
            "busy_type": "Tentative",
            "movability": "high",
            "source": "freebusy",
            "reschedule_hint_start": "2026-07-15T11:00:00+03:00",
            "reschedule_hint_end": "2026-07-15T12:00:00+03:00",
        }
    ]
    event = _fake_event(subject="Синк", busy_type="Tentative")
    with patch(
        "app.services.scheduled_meeting_plan_preview.conflicting_events_at_slot",
        return_value=[
            {
                "event_start": "2026-07-15T09:00:00+03:00",
                "event_end": "2026-07-15T10:00:00+03:00",
                "event_subject": "Синк",
                "busy_type": "Tentative",
                "movability": "high",
            }
        ],
    ):
        result = evaluate_occurrence_preview(
            occurrence=occurrence,
            conflict_policy="soft_week",
            attendees=["a@turbo-don.ru"],
            busy_by_attendee={"a@turbo-don.ru": _busy_wed()},
            events_by_attendee={"a@turbo-don.ru": [event]},
            config=_config(),
            anchor_weekday=ScheduledMeetingWeekday.WEDNESDAY,
        )
    assert result.status == "unresolved"
    assert result.recommended_option == "reschedule_blockers"
    assert result.conflicts[0].reschedule_hint_start is not None
    hint_kwargs = mock_hints.call_args.kwargs
    assert hint_kwargs["reserved_slot"][0] == occurrence.slot_start


@pytest.mark.asyncio
async def test_build_plan_preview_fetches_freebusy_once() -> None:
    meeting = _meeting()
    occ1 = _occurrence(date(2026, 7, 15))
    occ2 = _occurrence(date(2026, 7, 22))
    db = AsyncMock()

    with (
        patch(
            "app.services.scheduled_meeting_plan_preview.resolve_attendee_emails",
            AsyncMock(return_value=["a@turbo-don.ru", "b@turbo-don.ru"]),
        ),
        patch(
            "app.services.scheduled_meeting_plan_preview.build_occurrences_from_rule",
            return_value=[occ1, occ2],
        ),
        patch(
            "app.services.scheduled_meeting_plan_preview.load_config",
            return_value=_config(),
        ),
        patch(
            "app.services.scheduled_meeting_plan_preview.busy_intervals_and_events_from_freebusy",
            return_value=(
                {"a@turbo-don.ru": [], "b@turbo-don.ru": []},
                {"a@turbo-don.ru": [], "b@turbo-don.ru": []},
            ),
        ) as mock_freebusy,
        patch(
            "app.services.scheduled_meeting_plan_preview.asyncio.to_thread",
            new=AsyncMock(side_effect=lambda fn, *args, **kwargs: fn(*args, **kwargs)),
        ),
    ):
        result = await build_plan_preview(db, meeting, conflict_policy="soft_week")

    assert mock_freebusy.call_count == 1
    assert result.summary["total"] == 2
    assert result.summary["ok"] == 2
    assert all(item.status == "ok" for item in result.occurrences)


@pytest.mark.asyncio
async def test_service_plan_preview_rejects_non_created() -> None:
    db = AsyncMock()
    meeting_id = uuid.uuid4()
    meeting = _meeting(id=meeting_id, status=ScheduledMeetingStatus.PLANNED)
    loaded = MagicMock()
    loaded.scalar_one_or_none.return_value = meeting
    db.execute = AsyncMock(return_value=loaded)

    with pytest.raises(Exception) as exc_info:
        await ScheduledMeetingService(db).plan_preview(
            meeting_id,
            ScheduledMeetingPlanPreviewRequest(conflict_policy="soft_week"),
        )
    assert getattr(exc_info.value, "status_code", None) == 409
