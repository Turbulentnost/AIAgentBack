"""Тесты единого LLM-промпта (analyze_incoming)."""

from __future__ import annotations

from datetime import datetime, timezone

from agent_pochta.schemas import EmailMessage
from agent_pochta.services.llm_analyze import (
    build_analyze_messages,
    extract_partner_from_summary,
    extract_partner_from_text_fields,
    infer_partner_from_domain,
    infer_partner_from_email,
    is_own_organization,
    looks_like_job_title,
    looks_like_org_name,
    looks_like_person_name,
    normalize_partner_name,
    parse_analyze_response,
    resolve_partner_ladder,
    resolve_partner_name,
)
from agent_pochta.services.summary import extract_partner_from_signature


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


def test_analyze_system_prompt_forbids_chat_replies():
    system, _user = build_analyze_messages(
        _email(),
        "Просим выставить счёт.",
        [{"department_id": "SALES", "department_name": "Продажи"}],
    )
    assert "внутренний классификатор" in system
    assert "только JSON" in system
    assert "Здравствуйте" in system
    assert "Спасибо за ваше сообщение" in system
    assert "не веди диалог" in system.lower()
    assert "allowlist" in system
    assert "действие, требуемое в письме" in system
    assert "Действие требуемое в письме: краткая тема" in system
    assert "не ставь шаблонное «Действие»" in system
    assert "partner_name" in system
    assert "Лесенка" in system
    assert "БелГИМ" in system
    assert "process_type" in system
    assert "сух" in system.lower()
    assert "мусорн" in system.lower()
    # код организации (НП/АЛ/…) не в схеме ответа LLM
    assert '"organization"' not in system


def test_parse_rejects_chat_style_summary_ru():
    analysis = parse_analyze_response(
        {
            "is_spam": False,
            "spam_confidence": 0.1,
            "department_id": "SALES",
            "department_name": "Продажи",
            "dept_confidence": 0.9,
            "reasoning": "тест",
            "summary_ru": (
                "Здравствуйте, Имя! Спасибо за ваше сообщение. "
                "Вам может потребоваться обратиться к руководителю отдела персонала."
            ),
        },
        candidates=[{"department_id": "SALES", "department_name": "Продажи"}],
        subject="Вопрос",
        combined_text="Вопрос по кадрам.",
    )
    assert analysis.summary_ru == ""


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
        "xml_theme": "Запрос: Счёт на оплату",
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
    assert analysis.xml_theme.startswith("Запрос:")
    assert "Счёт" in analysis.xml_theme
    assert analysis.process_type == "исполнение"


def test_parse_missing_spam_confidence_defaults_not_spam():
    analysis = parse_analyze_response(
        {
            "is_spam": False,
            "spam_reason": "Деловой запрос",
            "department_id": "SALES",
            "department_name": "Продажи",
            "dept_confidence": 0.0,
            "summary_ru": "Клиент просит акт сверки.",
        },
        candidates=[{"department_id": "SALES", "department_name": "Продажи"}],
        subject="Акт",
        combined_text="Просим акт сверки.",
    )
    assert analysis.spam.is_spam is False
    assert analysis.spam.confidence == 0.05
    assert analysis.routing.confidence == 0.0


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


def test_parse_analyze_process_type_heuristic_default_rassmotreniye():
    analysis = parse_analyze_response(
        {
            "department_id": "SALES",
            "department_name": "Продажи",
            "dept_confidence": 0.7,
            "reasoning": "Общий запрос",
            "summary_ru": "Контрагент уточняет условия поставки.",
        },
        candidates=[{"department_id": "SALES", "department_name": "Продажи"}],
        subject="Re: условия поставки",
        combined_text="Добрый день, подскажите сроки и условия поставки оборудования.",
    )
    assert analysis.process_type == "рассмотрение"


def test_parse_analyze_process_type_act_sverki_rassmotreniye():
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
        combined_text="Направляем акт сверки за квартал во вложении.",
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
    assert analysis.xml_theme.startswith("Запрос:")
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
    body = "Добрый день! Просим выставить счёт."
    email = _email(sender_email="info@gazprom-neft.ru", sender_name="", body_text=body)
    assert (
        resolve_partner_name(
            llm_partner="ПАО «Газпром нефть»",
            rag_partner="Старый контрагент",
            email=email,
            body_text=body,
        )
        == "ПАО «Газпром нефть»"
    )


def test_resolve_partner_falls_back_to_rag():
    email = _email(sender_email="client@gmail.com")
    assert (
        resolve_partner_name(
            llm_partner="",
            rag_partner="ООО Пример",
            email=email,
        )
        == "ООО Пример"
    )


def test_resolve_partner_prefers_signature_over_llm_domain_guess():
    body = (
        "Добрый день! ОЛ 31222, 31340 отправлены в просчет.\n\n"
        "С уважением,\n"
        "Менеджер\n"
        "ООО ЛАН-Сервис"
    )
    email = _email(
        sender_email="sales@lan-service.ru",
        sender_name="Lan Service",
        body_text=body,
        subject="ОЛ 31222, 31240 в работу",
    )
    assert (
        resolve_partner_name(
            llm_partner="Lan Service",
            rag_partner="Lan Service",
            email=email,
            body_text=body,
        )
        == "ООО ЛАН-Сервис"
    )


def test_resolve_partner_prefers_signature_over_rag_and_domain():
    body = (
        "Добрый день! ОЛ 31222, 31340 отправлены в просчет.\n\n"
        "С уважением,\n"
        "Менеджер\n"
        "ООО ЛАН-Сервис"
    )
    email = _email(
        sender_email="sales@lan-service.ru",
        sender_name="Lan Service",
        body_text=body,
        subject="ОЛ 31222, 31240 в работу",
    )
    assert extract_partner_from_signature(body) == "ООО ЛАН-Сервис"
    assert (
        resolve_partner_name(
            llm_partner=None,
            rag_partner="Lan Service",
            email=email,
            body_text=body,
        )
        == "ООО ЛАН-Сервис"
    )
    assert infer_partner_from_email(email) == "Lan Service"


def test_resolve_partner_infers_from_sender_name():
    email = _email(sender_email="client@gmail.com", sender_name="ООО ТехноСервис")
    assert resolve_partner_name(llm_partner=None, rag_partner=None, email=email) == "ООО ТехноСервис"


def test_infer_partner_from_corporate_domain():
    email = _email(sender_email="billing@gazprom-neft.ru", sender_name="")
    assert infer_partner_from_email(email) == "Gazprom Neft"


def test_normalize_partner_rejects_dash():
    assert normalize_partner_name("-") is None
    assert normalize_partner_name("неизвестно") is None


def test_looks_like_person_name():
    assert looks_like_person_name("Oksana Popova") is True
    assert looks_like_person_name("Оксана Попова") is True
    assert looks_like_person_name("ООО «Ромашка»") is False
    assert looks_like_person_name("H-Energy") is False


def test_looks_like_job_title_not_org():
    title = "Инженер 1 категории НИЦИСИиТ, БелГИМ"
    assert looks_like_job_title(title) is True
    assert looks_like_org_name(title) is False
    assert looks_like_org_name("БелГИМ") is True
    assert looks_like_org_name("ООО МедТрансСервис") is True


def test_extract_partner_from_summary_iz_ooo():
    assert (
        extract_partner_from_summary(
            "Валентина Рыжих из ООО МедТрансСервис просит согласовать счёт."
        )
        == "ООО МедТрансСервис"
    )


def test_resolve_partner_prefers_summary_over_wrong_llm_org():
    email = _email(sender_email="user@gmail.com", sender_name="Валентина Рыжих")
    assert (
        resolve_partner_name(
            llm_partner='ООО "ИТЦ"',
            rag_partner=None,
            email=email,
            summary_ru=(
                "Валентина Рыжих из ООО МедТрансСервис просит выставить закрывающие документы."
            ),
        )
        == "ООО МедТрансСервис"
    )


def test_resolve_partner_rejects_llm_job_title():
    email = _email(sender_email="user@gmail.com", sender_name="")
    assert (
        resolve_partner_name(
            llm_partner="Инженер 1 категории НИЦИСИиТ, БелГИМ",
            rag_partner=None,
            email=email,
            summary_ru="Сотрудник от компании БелГИМ запрашивает документы.",
        )
        == "БелГИМ"
    )


def test_infer_partner_from_domain_h_energy():
    assert infer_partner_from_domain("forte@h-energy.ru") == "H-Energy"


def test_infer_partner_from_email_prefers_domain_over_person_from():
    email = _email(
        sender_email="forte@h-energy.ru",
        sender_name="Oksana Popova",
    )
    assert infer_partner_from_email(email) == "H-Energy"


def test_resolve_partner_rejects_llm_person_name_for_corporate_domain():
    body = "Добрый день, направляем документы."
    email = _email(
        sender_email="forte@h-energy.ru",
        sender_name="Oksana Popova",
        body_text=body,
    )
    assert (
        resolve_partner_name(
            llm_partner="Oksana Popova",
            rag_partner=None,
            email=email,
            body_text=body,
            summary_ru="Оксана Попова от компании H-Energy отвечает на запрос.",
        )
        == "H-Energy"
    )


def test_resolve_partner_uses_summary_company_when_domain_unknown():
    email = _email(sender_email="user@gmail.com", sender_name="Oksana Popova")
    assert (
        resolve_partner_name(
            llm_partner="Oksana Popova",
            rag_partner=None,
            email=email,
            summary_ru="Оксана Попова от компании H-Energy отвечает на запрос.",
        )
        == "H-Energy"
    )


def test_is_own_organization_excludes_turbulence_and_almaz():
    assert is_own_organization('ООО НПО «Турбулентность-ДОН»') is True
    assert is_own_organization('ООО "АЛМАЗ"') is True
    assert is_own_organization("ООО «Ромашка»") is False


def test_resolve_partner_ladder_explicit_company():
    email = _email(sender_name="Иван Иванов")
    assert (
        resolve_partner_ladder(
            explicit_partner="ООО «Ромашка»",
            email=email,
        )
        == "ООО «Ромашка»"
    )


def test_resolve_partner_ladder_finds_ooo_in_text():
    email = _email(
        sender_name="Менеджер",
        subject="Счёт",
        body_text="Направляем счёт от ООО «ГазСервис».",
    )
    assert (
        resolve_partner_ladder(
            explicit_partner=None,
            email=email,
        )
        == "ООО «ГазСервис»"
    )


def test_resolve_partner_ladder_sender_name_fallback():
    email = _email(sender_name="Пётр Сидоров", body_text="Добрый день!")
    assert (
        resolve_partner_ladder(
            explicit_partner=None,
            email=email,
        )
        == "Пётр Сидоров"
    )


def test_extract_partner_from_text_fields_skips_own_org():
    assert (
        extract_partner_from_text_fields(
            body_text="ООО НПО «Турбулентность-ДОН» просит оплатить счёт ООО «Лунда».",
        )
        == "ООО «Лунда»"
    )
