from __future__ import annotations

from app.tools.Outlook.outlook_html_body import (
    INVITE_FONT_FAMILY,
    append_plain_text_to_html,
    plain_text_to_html,
)


def test_plain_text_to_html_uses_arial_font() -> None:
    body = plain_text_to_html("Строка 1\nСтрока 2\n\nАбзац 2")
    html = str(body)
    assert INVITE_FONT_FAMILY in html
    assert "Arial" in html
    assert "Строка 1<br>Строка 2" in html
    assert "<p>Абзац 2</p>" in html


def test_plain_text_to_html_escapes_angle_brackets_in_emails() -> None:
    body = plain_text_to_html(
        "Комарькова Анастасия Эдуардовна <sktb_razvitie10@turbo-don.ru>"
    )
    html = str(body)
    assert "Комарькова Анастасия Эдуардовна &lt;sktb_razvitie10@turbo-don.ru&gt;" in html
    assert "<sktb_razvitie10@turbo-don.ru>" not in html


def test_append_plain_text_to_html_preserves_existing_and_uses_arial() -> None:
    body = append_plain_text_to_html("<p>старое</p>", "новое сообщение")
    html = str(body)
    assert "<p>старое</p>" in html
    assert "Arial" in html
    assert "новое сообщение" in html
