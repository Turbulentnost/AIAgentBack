from app.schemas.meeting import (
    MeetingSlotBlockingEventRead,
    MeetingSlotParticipantStatusRead,
)
from app.services.meeting_agent_slot_confirm import (
    build_slot_confirm_state,
    collect_slot_conflict_reschedule_targets,
)


def _blocking_event(**kwargs) -> MeetingSlotBlockingEventRead:
    defaults = {
        "event_subject": "Тема 1",
        "event_start_iso": "2026-07-17T15:19:00+03:00",
        "event_end_iso": "2026-07-17T16:19:00+03:00",
        "reschedule_hint_start": "2026-07-20T13:00:00+03:00",
        "reschedule_hint_end": "2026-07-20T14:00:00+03:00",
        "source": "company_calendar",
    }
    defaults.update(kwargs)
    return MeetingSlotBlockingEventRead(**defaults)


def _participant(**kwargs) -> MeetingSlotParticipantStatusRead:
    defaults = {
        "fio": "Соломичева",
        "email": "a@turbo-don.ru",
        "role": "manager",
        "role_label": "Руководитель",
        "status": "busy",
        "blocking_events": [_blocking_event()],
    }
    defaults.update(kwargs)
    return MeetingSlotParticipantStatusRead(**defaults)


def test_collect_reschedule_targets_deduplicates_same_meeting() -> None:
    participants = [
        _participant(fio="A", email="a@turbo-don.ru"),
        _participant(fio="B", email="b@turbo-don.ru"),
        _participant(fio="C", email="c@turbo-don.ru"),
    ]
    targets = collect_slot_conflict_reschedule_targets(participants)
    assert len(targets) == 1
    assert targets[0].event_subject == "Тема 1"


def test_build_slot_confirm_state_allows_reschedule_when_hints_present() -> None:
    participants = [_participant()]
    can_confirm, requires_reschedule = build_slot_confirm_state(
        participants,
        room=None,
        slot_available=False,
    )
    assert can_confirm is True
    assert requires_reschedule is True


def test_build_slot_confirm_state_blocks_without_hints() -> None:
    participants = [
        _participant(
            blocking_events=[
                _blocking_event(
                    reschedule_hint_start=None,
                    reschedule_hint_end=None,
                    source="interval",
                )
            ]
        )
    ]
    can_confirm, requires_reschedule = build_slot_confirm_state(
        participants,
        room=None,
        slot_available=False,
    )
    assert can_confirm is False
    assert requires_reschedule is False
