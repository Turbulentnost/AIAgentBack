from app.services.meeting_agent_approve import build_approve_invite_body, resolve_approve_recipients
from app.schemas.meeting import MeetingAgentSlotApproveRequest, MeetingAttendeeRead


def test_resolve_approve_recipients_from_attendees() -> None:
    payload = MeetingAgentSlotApproveRequest(
        slot_start="2026-06-20 11:00",
        slot_end="2026-06-20 11:20",
        attendees=[
            MeetingAttendeeRead(
                fio="A",
                email="a@turbo-don.ru",
                role="initiator",
                role_label="Инициатор",
                found=True,
            )
        ],
    )

    details, resolved = resolve_approve_recipients(payload)

    assert len(details) == 1
    assert resolved[0].email == "a@turbo-don.ru"


def test_resolve_approve_recipients_from_emails() -> None:
    payload = MeetingAgentSlotApproveRequest(
        slot_start="2026-06-20 11:00",
        slot_end="2026-06-20 11:20",
        attendee_emails=["a@turbo-don.ru", "b@turbo-don.ru"],
    )

    _details, resolved = resolve_approve_recipients(payload)

    assert [item.email for item in resolved] == ["a@turbo-don.ru", "b@turbo-don.ru"]


def test_build_approve_invite_body_includes_room_as_participant() -> None:
    body = build_approve_invite_body(
        [
            MeetingAttendeeRead(
                fio="Иванов Иван",
                email="ivanov@turbo-don.ru",
                role="participant",
                role_label="Участник",
                found=True,
            )
        ],
        room={"name": "Зал совещаний КБ", "email": "konfzalkb@turbo-don.ru"},
    )

    assert "Иванов Иван <ivanov@turbo-don.ru>;" in body
    assert "Зал совещаний КБ <konfzalkb@turbo-don.ru>" in body
