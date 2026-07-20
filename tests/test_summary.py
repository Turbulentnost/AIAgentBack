"""Тесты подготовки и ограничения краткого обзора."""

from __future__ import annotations

from datetime import datetime, timezone

from agent_pochta.schemas import Attachment, EmailMessage, RoutingResult, Priority
from agent_pochta.services.summary import build_summary_context, clamp_summary, prepare_text_for_summary


def test_prepare_text_strips_signature():
    text = "Просим выставить счёт на поставку по договору номер 123 от организации."
    text += "\n\nС уважением,\nИванов"
    assert "Иванов" not in prepare_text_for_summary(text)


def test_clamp_summary_limits_sentences():
    text = "Первое. Второе. Третье. Четвёртое. Пятое. Шестое."
    result = clamp_summary(text, max_sentences=3, max_chars=500)
    assert result.count(".") == 3
    assert "Шестое" not in result


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
