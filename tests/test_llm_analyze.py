"""Тесты единого LLM-промпта (analyze_incoming)."""

from __future__ import annotations

from datetime import datetime, timezone

from agent_pochta.schemas import EmailMessage
from agent_pochta.services.llm_analyze import (
    infer_partner_from_email,
    normalize_partner_name,
    parse_analyze_response,
    resolve_partner_name,
)


def _email(**kw) -> EmailMessage:
    base = dict(
        message_id="<a@example>",
        mailbox="info@turbo-don.ru",
        sender_email="client@example.com",
        subject="Счёт",
        body_text="Просим выставить счёт.",
        received_at=datetime.now(timezone.utc),
    )
    base.update(kw)
    return EmailMessage(**base)


def test_parse_full_analyze_response():
    data = {
        "is_spam": False,
        "spam_confidence": 0.1,
        "spam_reason": "Деловой запрос",
        "department_id": "SALES",
        "department_name": "Продажи",
        "dept_confidence": 0.91,
        "reasoning": "Запрос на счёт",
        "summary_ru": "Клиент просит счёт. Нужно выставить документ.",
        "xml_theme": (
            "Клиент просит выставить счёт на поставку оборудования - "
            "Запрос на выставление счёта"
        ),
        "process_type": "исполнение",
    }
    candidates = [{"department_id": "SALES", "department_name": "Продажи"}]
    analysis = parse_analyze_response(
        data,
        candidates=candidates,
        subject="Счёт",
        combined_text="Просим выставить счёт.",
    )

    assert analysis.spam.is_spam is False
    assert analysis.routing.department_id == "SALES"
    assert analysis.routing.confidence == 0.91
    assert "счёт" in analysis.summary_ru.lower()
    assert " - " in analysis.xml_theme
    assert analysis.xml_theme.endswith("Запрос на выставление счёта")
    assert analysis.process_type == "исполнение"


def test_parse_analyze_process_type_oznakomleniye():
    analysis = parse_analyze_response(
        {
            "department_id": "PROD",
            "department_name": "Сопровождение производства",
            "dept_confidence": 0.88,
            "reasoning": "Уведомление о поставке",
            "summary_ru": "Информация о сроках отгрузки по счёту.",
            "process_type": "ознакомление",
        },
        candidates=[{"department_id": "PROD", "department_name": "Сопровождение производства"}],
        subject="Информация о сроках отгрузки - Уведомление о поставке",
        combined_text="Сообщаем сроки отгрузки товара.",
    )
    assert analysis.process_type == "ознакомление"


def test_parse_analyze_process_type_heuristic_fallback():
    analysis = parse_analyze_response(
        {
            "department_id": "PROD",
            "department_name": "Сопровождение производства",
            "dept_confidence": 0.88,
            "reasoning": "Уведомление",
            "summary_ru": "Информация о сроках отгрузки.",
        },
        candidates=[{"department_id": "PROD", "department_name": "Сопровождение производства"}],
        subject="Информация о сроках отгрузки - Уведомление о поставке",
        combined_text="Уведомляем о сроках отгрузки товара.",
    )
    assert analysis.process_type == "ознакомление"


def test_parse_analyze_process_type_claim_default():
    analysis = parse_analyze_response(
        {
            "department_id": "LEGAL",
            "department_name": "Юридический",
            "dept_confidence": 0.9,
            "reasoning": "Претензия",
            "summary_ru": "Претензия по качеству.",
        },
        candidates=[{"department_id": "LEGAL", "department_name": "Юридический"}],
        subject="Письмо без явных маркеров",
        combined_text="Текст без ключевых слов.",
        claim=True,
    )
    assert analysis.process_type == "рассмотрение"


def test_parse_analyze_fills_xml_theme_when_missing():
    analysis = parse_analyze_response(
        {
            "department_id": "FINANCE",
            "department_name": "Финансы",
            "dept_confidence": 0.8,
            "reasoning": "Акт сверки",
            "summary_ru": "Акт сверки за квартал.",
        },
        candidates=[{"department_id": "FINANCE", "department_name": "Финансы"}],
        subject="Акт сверки",
        combined_text="Направляем акт сверки за квартал.",
    )
    assert " - " in analysis.xml_theme
    assert "акт сверки" in analysis.xml_theme.lower()


def test_parse_trusted_skips_spam_fields():
    analysis = parse_analyze_response(
        {
            "department_id": "FINANCE",
            "department_name": "Финансы",
            "dept_confidence": 0.8,
            "reasoning": "Акт сверки",
            "summary_ru": "Акт сверки за квартал.",
        },
        candidates=[{"department_id": "FINANCE", "department_name": "Финансы"}],
        skip_spam_check=True,
    )
    assert analysis.spam.rule_hit == "trusted_sender"
    assert analysis.spam.is_spam is False


def test_parse_analyze_extracts_partner_name():
    analysis = parse_analyze_response(
        {
            "department_id": "SALES",
            "department_name": "Продажи",
            "dept_confidence": 0.9,
            "reasoning": "Счёт",
            "summary_ru": "Счёт от контрагента.",
            "partner_name": "ООО «Ромашка»",
        },
        candidates=[{"department_id": "SALES", "department_name": "Продажи"}],
    )
    assert analysis.partner_name == "ООО «Ромашка»"


def test_resolve_partner_prefers_llm_over_rag():
    email = _email(sender_email="info@gazprom-neft.ru", sender_name="")
    assert (
        resolve_partner_name(
            llm_partner="ПАО «Газпром нефть»",
            rag_partner="Старый контрагент",
            email=email,
        )
        == "ПАО «Газпром нефть»"
    )


def test_resolve_partner_falls_back_to_rag():
    email = _email()
    assert (
        resolve_partner_name(
            llm_partner="",
            rag_partner="ООО Пример",
            email=email,
        )
        == "ООО Пример"
    )


def test_resolve_partner_infers_from_sender_name():
    email = _email(sender_name="ООО ТехноСервис")
    assert resolve_partner_name(llm_partner=None, rag_partner=None, email=email) == "ООО ТехноСервис"


def test_infer_partner_from_corporate_domain():
    email = _email(sender_email="billing@gazprom-neft.ru", sender_name="")
    assert infer_partner_from_email(email) == "Gazprom Neft"


def test_normalize_partner_rejects_dash():
    assert normalize_partner_name("-") is None
    assert normalize_partner_name("неизвестно") is None
