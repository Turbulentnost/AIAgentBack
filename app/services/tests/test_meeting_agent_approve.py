from app.services.meeting_agent_approve import resolve_approve_recipients
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
