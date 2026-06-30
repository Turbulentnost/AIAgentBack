from __future__ import annotations

from app.agents.meeting_agent.backend import ResolvedParticipant
from app.schemas.meeting import MeetingAgentSlotApproveRequest, MeetingAttendeeRead
from app.services.meeting_invite_format import invite_body_from_attendees

ATTENDEE_ROLE_LABELS = {
    "initiator": "Инициатор",
    "manager": "Руководитель",
    "participant": "Участник",
}


class MeetingApproveError(ValueError):
    """Ошибка подготовки утверждения слота (участники, e-mail)."""


def resolve_approve_recipients(
    payload: MeetingAgentSlotApproveRequest,
) -> tuple[list[MeetingAttendeeRead], list[ResolvedParticipant]]:
    if payload.attendees:
        missing_emails: list[str] = []
        resolved: list[ResolvedParticipant] = []
        seen_emails: set[str] = set()
        for attendee in payload.attendees:
            email = (attendee.email or "").strip()
            if not email:
                missing_emails.append(attendee.fio)
                continue
            if email.lower() in seen_emails:
                continue
            seen_emails.add(email.lower())
            resolved.append(ResolvedParticipant(fio=attendee.fio, email=email, found=True))
        if missing_emails or not resolved:
            missing = missing_emails or [item.fio for item in payload.attendees]
            raise MeetingApproveError("Не указан e-mail для: " + ", ".join(missing))
        return payload.attendees, resolved

    emails: list[str] = []
    seen_emails: set[str] = set()
    for raw in payload.attendee_emails or []:
        email = raw.strip()
        if not email or email.lower() in seen_emails:
            continue
        seen_emails.add(email.lower())
        emails.append(email)
    if not emails:
        raise MeetingApproveError("Список attendee_emails пуст")

    attendee_details = [
        MeetingAttendeeRead(
            fio=email,
            email=email,
            role="participant",
            role_label=ATTENDEE_ROLE_LABELS["participant"],
            found=True,
        )
        for email in emails
    ]
    resolved = [ResolvedParticipant(fio=email, email=email, found=True) for email in emails]
    return attendee_details, resolved


def build_approve_invite_body(attendees: list[MeetingAttendeeRead]) -> str:
    return invite_body_from_attendees(attendees)
