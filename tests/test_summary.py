"""Тесты подготовки и ограничения краткого обзора."""

from __future__ import annotations

from datetime import datetime, timezone

from agent_pochta.schemas import Attachment, EmailMessage, RoutingResult, Priority
from agent_pochta.services.summary import (
    build_summary_context,
    clamp_summary,
    extract_partner_from_signature,
    looks_like_chat_reply,
    prepare_text_for_summary,
    sanitize_summary_ru,
)


def test_prepare_text_strips_signature():
    text = "Просим выставить счёт на поставку по договору номер 123 от организации."
    text += "\n\nС уважением,\nИванов"
    assert "Иванов" not in prepare_text_for_summary(text)


def test_extract_partner_from_signature_lan_service():
    body = (
        "Добрый день! ОЛ 31222, 31340 отправлены в просчет.\n\n"
        "С уважением,\n"
        "Менеджер\n"
        "ООО ЛАН-Сервис"
    )
    assert extract_partner_from_signature(body) == "ООО ЛАН-Сервис"


def test_extract_partner_from_signature_karbin():
    body = (
        "Просим согласовать спецификацию.\n\n"
        "С уважением,\n"
        "Иванов И.И.\n"
        "ООО «Карбин»\n"
        "тел. +7 (863) 123-45-67"
    )
    assert extract_partner_from_signature(body) == "ООО «Карбин»"


def test_build_summary_context_includes_email_signature():
    body = "Текст запроса.\n\nС уважением,\nООО ЛАН-Сервис"
    email = EmailMessage(
        message_id="<sig@example>",
        mailbox="info@turbo-don.ru",
        sender_email="sales@lan-service.ru",
        subject="ОЛ 31222",
        body_text=body,
        received_at=datetime.now(timezone.utc),
    )
    ctx = build_summary_context(email, body)
    assert "ООО ЛАН-Сервис" in ctx["email_signature"]


def test_clamp_summary_limits_sentences():
    text = "Первое. Второе. Третье. Четвёртое. Пятое. Шестое."
    result = clamp_summary(text, max_sentences=3, max_chars=500)
    assert result.count(".") == 3
    assert "Шестое" not in result


def test_looks_like_chat_reply_detects_greeting_assistant():
    bad = (
        "Здравствуйте, Роман! Спасибо за ваше сообщение. "
        "В «Турбулентность Дон» вам может потребоваться обратиться "
        "к руководителю отдела персонала."
    )
    assert looks_like_chat_reply(bad) is True
    assert sanitize_summary_ru(bad) == ""
    assert clamp_summary(bad) == ""


def test_clamp_summary_keeps_official_office_overview():
    good = (
        "Роман (внешний отправитель) спрашивает о порядке обращения в отдел персонала. "
        "Требуется маршрутизировать письмо в профильный отдел."
    )
    assert looks_like_chat_reply(good) is False
    assert sanitize_summary_ru(good) == good
    assert "отдел персонала" in clamp_summary(good)


def test_build_summary_context_includes_attachment_text():
    content = "Акт сверки за 1 квартал.".encode("utf-8")
    email = EmailMessage(
        message_id="<t@example>",
        mailbox="info@turbo-don.ru",
        sender_email="a@b.ru",
        subject="Заказ",
        body_text="Текст",
        received_at=datetime.now(timezone.utc),
        attachments=[
            Attachment(
                filename="akt.txt",
                mime_type="text/plain",
                size_bytes=len(content),
                content=content,
                extracted_text="Акт сверки за 1 квартал.",
            )
        ],
    )
    combined = "Текст\n\n[Вложение akt.txt]\nАкт сверки за 1 квартал."
    ctx = build_summary_context(email, combined, attachments_text="[Вложение akt.txt]\nАкт сверки")
    assert ctx["has_attachment_text"] is True
    assert "Акт сверки" in ctx["attachments_text"]
    assert ctx["attachments"][0]["text_excerpt"]


def test_build_summary_context_includes_routing():
    email = EmailMessage(
        message_id="<t@example>",
        mailbox="info@turbo-don.ru",
        sender_email="a@b.ru",
        subject="Заказ",
        body_text="Текст",
        received_at=datetime.now(timezone.utc),
    )
    ctx = build_summary_context(
        email,
        email.body_text,
        routing=RoutingResult(
            department_id="SALES",
            department_name="Отдел продаж",
            confidence=0.9,
            reasoning="",
            priority=Priority.NORMAL,
        ),
    )
    assert ctx["department_name"] == "Отдел продаж"
    assert ctx["priority"] == "normal"


def test_build_summary_context_preserves_attachments_on_long_body():
    """Текст вложений не должен обрезаться из-за длинного тела письма."""
    long_body = "Пояснение. " * 3000
    marker = "УНИКАЛЬНЫЙ_НОМЕР_СЧЁТА_998877"
    email = EmailMessage(
        message_id="<long@example>",
        mailbox="info@turbo-don.ru",
        sender_email="a@b.ru",
        subject="Счёт",
        body_text=long_body,
        received_at=datetime.now(timezone.utc),
        attachments=[
            Attachment(
                filename="schet.pdf",
                mime_type="application/pdf",
                size_bytes=100,
                extracted_text=f"Счёт на оплату {marker}.",
            )
        ],
    )
    combined = f"{long_body}\n\n=== ВЛОЖЕНИЯ ===\n{marker}"
    ctx = build_summary_context(email, combined)
    assert marker in ctx["attachments_text"]
    assert marker in ctx["body_and_attachments"]
    assert len(ctx["attachments_text"]) >= len(marker)
