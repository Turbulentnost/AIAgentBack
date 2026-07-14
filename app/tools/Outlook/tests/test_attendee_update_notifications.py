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
    stakeholder_notification_recipients,
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


def test_build_existing_attendees_notification_body_for_addition() -> None:
    body = build_existing_attendees_notification_body(
        subject="Согласование ТЗ",
        slot_label="14.07.2026, 09:30–10:30",
        added_pairs=[("Попов Павел Павлович", "popov@turbo-don.ru")],
        removed_pairs=[],
        roster_pairs=[
            ("Иванов Иван Иванович", "ivanov@turbo-don.ru"),
            ("Сидоров Сидор Сидорович", "sidorov@turbo-don.ru"),
            ("Попов Павел Павлович", "popov@turbo-don.ru"),
        ],
    )
    assert (
        'Состав участников совещания 14.07.2026, 09:30–10:30 по теме "Согласование ТЗ" был изменен. '
        "Обновленный состав:"
    ) in body
    assert "Попов Павел Павлович <popov@turbo-don.ru>" in body
    assert "Добавленные участники:" in body
    assert "Новые участники:" not in body


def test_build_existing_attendees_notification_body_for_removal() -> None:
    body = build_existing_attendees_notification_body(
        subject="Согласование ТЗ",
        slot_label="14.07.2026, 09:30–10:30",
        added_pairs=[],
        removed_pairs=[("Петров Петр Петрович", "petrov@turbo-don.ru")],
        roster_pairs=[
            ("Иванов Иван Иванович", "ivanov@turbo-don.ru"),
            ("Сидоров Сидор Сидорович", "sidorov@turbo-don.ru"),
        ],
    )
    assert (
        'Состав участников совещания 14.07.2026, 09:30–10:30 по теме "Согласование ТЗ" был изменен. '
        "Обновленный состав:"
    ) in body
    assert "Иванов Иван Иванович <ivanov@turbo-don.ru>" in body
    assert "Сидоров Сидор Сидорович <sidorov@turbo-don.ru>" in body
    assert "Удаленные участники:" in body
    assert "Петров Петр Петрович <petrov@turbo-don.ru>" in body
    assert "Исключённые участники:" not in body


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


def test_stakeholder_notification_recipients_prefers_stakeholders_on_add() -> None:
    recipients = stakeholder_notification_recipients(
        stakeholder_emails=["manager@turbo-don.ru", "initiator@turbo-don.ru"],
        before=["a@co.ru", "b@co.ru"],
        added=["c@co.ru"],
        removed=[],
    )
    assert recipients == ["manager@turbo-don.ru", "initiator@turbo-don.ru"]


def test_stakeholder_notification_recipients_prefers_stakeholders_on_remove() -> None:
    recipients = stakeholder_notification_recipients(
        stakeholder_emails=["manager@turbo-don.ru", "initiator@turbo-don.ru"],
        before=["a@co.ru", "b@co.ru"],
        added=[],
        removed=["b@co.ru"],
    )
    assert recipients == ["manager@turbo-don.ru", "initiator@turbo-don.ru"]


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
        slot_label="14.07.2026, 09:30–10:30",
    )
    assert (
        'Вы были исключены из участников совещания 14.07.2026, 09:30–10:30 '
        'по теме "Тестовая СЗ: проверка агента совещаний"'
        in body
    )
    assert INVITE_AGENT_FOOTER in body


def test_build_removed_attendees_calendar_body_contains_exclusion_text() -> None:
    item = SimpleNamespace(
        subject="Тестовая СЗ",
        start=None,
        end=None,
    )
    body = build_removed_attendees_calendar_body(item=item)
    html = str(body)
    assert "Arial" in html
    assert "Вы были исключены из участников совещания по теме" in html
    assert INVITE_AGENT_FOOTER in html


def test_build_new_attendees_calendar_invite_body_uses_standard_invite_format() -> None:
    item = SimpleNamespace(
        subject="Тестовая СЗ",
        required_attendees=[
            attendee("ivanov@turbo-don.ru", "Иванов Иван Иванович"),
            attendee("popov@turbo-don.ru", "Попов Павел Павлович"),
        ],
        optional_attendees=[],
    )
    body = build_new_attendees_calendar_invite_body(
        item=item,
        changes={"after": ["ivanov@turbo-don.ru", "popov@turbo-don.ru"]},
        account=None,
    )
    html = str(body)
    assert "Arial" in html
    assert "Иванов Иван Иванович <ivanov@turbo-don.ru>" in html
    assert "Попов Павел Павлович <popov@turbo-don.ru>" in html
    assert INVITE_AGENT_FOOTER in html
    assert "Вы были добавлены участником" not in html
