from app.tools.mail_templates import (
    INVITE_AGENT_FOOTER,
    default_reschedule_comment,
    invite_agent_footer,
    render_mail_template,
)


def test_invite_agent_footer_matches_template_file() -> None:
    assert invite_agent_footer() == INVITE_AGENT_FOOTER
    assert INVITE_AGENT_FOOTER


def test_render_mail_template_substitutes_placeholders() -> None:
    text = render_mail_template(
        "reject_memo_notification",
        memo_number="000009853",
        reason="Не указана тема",
    )
    assert "000009853" in text
    assert "Не указана тема" in text
    assert "{{" not in text


def test_render_mail_template_collapses_extra_blank_lines() -> None:
    text = render_mail_template(
        "attendee_roster_changed_generic",
        new_participants_section="",
        removed_participants_section="",
        roster_lines="A <a@co.ru>",
        extra_message="",
        footer="",
    )
    assert "Произошло обновление состава участников" in text
    assert "Обновленный состав:" in text
    assert "\n\n\n" not in text


def test_default_reschedule_comment() -> None:
    assert default_reschedule_comment() == (
        "Встреча перенесена для освобождения слота по служебной записке"
    )
