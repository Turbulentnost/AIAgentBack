from __future__ import annotations

from types import SimpleNamespace

from exchangelib.properties import Attendee, Mailbox

from app.tools.Outlook.attendee_update_notifications import (
    INVITE_AGENT_FOOTER,
    attendee_line,
    build_existing_attendees_notification_body,
    build_new_attendees_calendar_invite_body,
    build_new_attendees_notification_body,
    build_removed_attendees_calendar_body,
    build_removed_attendees_notification_body,
    existing_attendee_recipients,
    resolve_attendee_pair,
)


def attendee(email: str, name: str = "") -> Attendee:
    return Attendee(mailbox=Mailbox(email_address=email, name=name))


def test_attendee_line_format() -> None:
    assert attendee_line("Лапина Арина Антоновна", "npo_manager@turbo-don.ru") == (
        "Лапина Арина Антоновна <npo_manager@turbo-don.ru>"
    )


def test_build_existing_attendees_notification_body() -> None:
    body = build_existing_attendees_notification_body(
        added_pairs=[("Лапина Арина Антоновна", "npo_manager@turbo-don.ru")],
        removed_pairs=[],
        roster_pairs=[
            ("Мангасарян Давид Каренович", "sktb_razvitie9@turbo-don.ru"),
            ("Комарькова Анастасия Эдуардовна", "sktb_razvitie10@turbo-don.ru"),
            ("Лапина Арина Антоновна", "npo_manager@turbo-don.ru"),
        ],
    )
    assert "Произошло обновление состава участников" in body
    assert "Новые участники:" in body
    assert "Лапина Арина Антоновна <npo_manager@turbo-don.ru>" in body
    assert "Обновленный состав:" in body
    assert INVITE_AGENT_FOOTER in body


def test_build_new_attendees_notification_body() -> None:
    body = build_new_attendees_notification_body(
        subject="Тестовая СЗ: проверка агента совещаний",
        roster_pairs=[
            ("Мангасарян Давид Каренович", "sktb_razvitie9@turbo-don.ru"),
            ("Комарькова Анастасия Эдуардовна", "sktb_razvitie10@turbo-don.ru"),
            ("Лапина Арина Антоновна", "npo_manager@turbo-don.ru"),
        ],
    )
    assert 'Вы были добавлены участником на совещание по теме "Тестовая СЗ: проверка агента совещаний"' in body
    assert "Участники:" in body
    assert INVITE_AGENT_FOOTER in body


def test_existing_attendee_recipients_excludes_added_and_removed() -> None:
    recipients = existing_attendee_recipients(
        before=["a@co.ru", "b@co.ru", "konfzalkb@turbo-don.ru"],
        added=["c@co.ru"],
        removed=["b@co.ru"],
    )
    assert recipients == ["a@co.ru"]


def test_resolve_attendee_pair_uses_item_display_name() -> None:
    item = SimpleNamespace(
        required_attendees=[attendee("keep@co.ru", "Комарькова Анастасия Эдуардовна")],
        optional_attendees=[],
    )
    assert resolve_attendee_pair("keep@co.ru", item=item, account=None) == (
        "Комарькова Анастасия Эдуардовна",
        "keep@co.ru",
    )


def test_build_removed_attendees_notification_body() -> None:
    body = build_removed_attendees_notification_body(
        subject="Тестовая СЗ: проверка агента совещаний",
    )
    assert (
        'Вы были исключены из участников совещания по теме "Тестовая СЗ: проверка агента совещаний"'
        in body
    )
    assert INVITE_AGENT_FOOTER in body


def test_build_removed_attendees_calendar_body_contains_exclusion_text() -> None:
    item = SimpleNamespace(subject="Тестовая СЗ")
    body = build_removed_attendees_calendar_body(item=item)
    html = str(body)
    assert "Вы были исключены из участников совещания по теме" in html
    assert INVITE_AGENT_FOOTER in html


def test_build_new_attendees_calendar_invite_body_contains_welcome_text() -> None:
    item = SimpleNamespace(
        subject="Тестовая СЗ",
        required_attendees=[attendee("new@co.ru", "Лапина Арина Антоновна")],
        optional_attendees=[],
    )
    body = build_new_attendees_calendar_invite_body(
        item=item,
        changes={"after": ["new@co.ru"]},
        account=None,
    )
    html = str(body)
    assert "Вы были добавлены участником на совещание по теме" in html
    assert "Тестовая СЗ" in html
    assert INVITE_AGENT_FOOTER in html
