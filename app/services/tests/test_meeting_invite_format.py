from app.services.meeting_invite_format import (
    INVITE_AGENT_FOOTER,
    format_invite_body,
    format_invite_location,
    format_invite_location_from_detail,
    invite_body_from_attendees,
    resolve_invite_subject,
)
from app.schemas.meeting import MeetingAttendeeRead


def test_format_invite_location_with_manager_and_place() -> None:
    location = format_invite_location(
        "Донцова Анна Егоровна",
        "кабинет директора НПО",
    )
    assert location == "Руководитель совещания Донцова Анна Егоровна, кабинет директора НПО"


def test_format_invite_body_matches_outlook_example() -> None:
    body = format_invite_body(
        [
            ("Соломичева Светлана Викторовна", "sktb_razvitie2@turbo-don.ru"),
            ("Комарькова Анастасия Эдуардовна", "sktb_razvitie10@turbo-don.ru"),
            ("Донцова Анна Егоровна", "uk_omto12@turbo-don.ru"),
        ]
    )
    assert "Соломичева Светлана Викторовна <sktb_razvitie2@turbo-don.ru>;" in body
    assert "Комарькова Анастасия Эдуардовна <sktb_razvitie10@turbo-don.ru>;" in body
    assert "Донцова Анна Егоровна <uk_omto12@turbo-don.ru>" in body
    assert body.endswith(INVITE_AGENT_FOOTER)


def test_resolve_invite_subject_from_detail() -> None:
    detail = {
        "title": "Согласование ТЗ на ИИ-агент",
        "number": "000010154",
        "application": {},
    }
    assert resolve_invite_subject(detail) == "Согласование ТЗ на ИИ-агент СЗ 000010154"


def test_resolve_invite_subject_skips_duplicate_sz() -> None:
    detail = {
        "title": "Согласование ТЗ на ИИ-агент СЗ 000010154",
        "number": "000010154",
        "application": {},
    }
    assert resolve_invite_subject(detail) == "Согласование ТЗ на ИИ-агент СЗ 000010154"


def test_resolve_invite_subject_keeps_sz_prefix_in_number() -> None:
    detail = {
        "title": "Еженедельное совещание",
        "number": "СЗ-001",
        "application": {},
    }
    assert resolve_invite_subject(detail) == "Еженедельное совещание СЗ-001"


def test_resolve_invite_subject_fallback_with_number() -> None:
    detail = {"title": None, "number": "000010154", "application": {}}
    assert resolve_invite_subject(detail) == "Совещание СЗ 000010154"


def test_format_invite_location_from_detail() -> None:
    detail = {
        "application": {
            "manager": {"full_name": "Донцова Анна Егоровна"},
            "location": "кабинет директора НПО",
        }
    }
    assert format_invite_location_from_detail(detail) == (
        "Руководитель совещания Донцова Анна Егоровна, кабинет директора НПО"
    )


def test_invite_body_from_attendees_read_model() -> None:
    attendees = [
        MeetingAttendeeRead(
            fio="A",
            email="a@turbo-don.ru",
            role="participant",
            role_label="Участник",
            found=True,
        )
    ]
    body = invite_body_from_attendees(attendees)
    assert "A <a@turbo-don.ru>" in body
