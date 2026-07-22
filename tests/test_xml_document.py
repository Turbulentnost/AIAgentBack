"""Тесты парсинга и сохранения XML document."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock

from agent_pochta.api.app import _payload_xml_fields, _row_to_dict
from agent_pochta.db.models import EmailMessageRow
from agent_pochta.db.repository import EmailRepository
from agent_pochta.routing import route_email
from agent_pochta.routing.models import ConfidenceLevel, RoutingDecision, ServiceRoute
from agent_pochta.routing.normalize import contains_claim_marker
from agent_pochta.routing.xml_builder import (
    RESERVE_DEPARTMENT_CODE,
    SPAM_DEPARTMENT_CODE,
    _format_mail_datetime_for_xml,
    build_stub_xml_theme,
    build_subject_xml_theme,
    build_xml_document,
    email_subject_theme,
    format_partner,
    normalize_xml_theme,
    sanitize_theme,
    service_reasoning,
    validate_xml_document,
)
from agent_pochta.routing.xml_parser import ensure_xml_document, parse_document_xml, rebuild_xml_document_from_row
from agent_pochta.schemas import EmailMessage, ProcessingStatus, RoutingResult, SpamResult
from agent_pochta.state import AgentState


SAMPLE_XML = (
    "<document>"
    "<organization>НП</organization>"
    "<theme>Тестовая тема</theme>"
    "<направление>ПР</направление>"
    "<claim>false</claim>"
    "<partner>ООО Пример</partner>"
    "<services>"
    "<service>"
    "<name>00-000076</name>"
    "<title>Юридический отдел</title>"
    "<process>исполнение</process>"
    "<reasoning>Тестовая тема письма</reasoning>"
    "</service>"
    "</services>"
    "<email_sender>sender@example.com</email_sender>"
    "<email_recipient>info@turbo-don.ru</email_recipient>"
    "<mail_datetime>2026-07-03 10:00:00</mail_datetime>"
    "<process>исполнение</process>"
    "</document>"
)

LEGACY_XML = (
    SAMPLE_XML.replace("</document>", "")
    + "<spam>false</spam>"
    + "<confidence_level>ВЫСОКАЯ</confidence_level>"
    + "<matching_keywords>договор; счёт</matching_keywords>"
    + "<processing_notes>Автоматическая регистрация разрешена.</processing_notes>"
    + "</document>"
)


def test_parse_document_xml_returns_structured_fields():
    parsed = parse_document_xml(SAMPLE_XML)
    assert parsed is not None
    assert parsed["organization"] == "НП"
    assert parsed["theme"] == "Тестовая тема"
    assert parsed["direction"] == "ПР"
    assert parsed["claim"] is False
    assert parsed["partner"] == "ООО Пример"
    assert parsed["services"] == [
        {
            "name": "00-000076",
            "title": "Юридический отдел",
            "process": "исполнение",
            "reasoning": "Тестовая тема письма",
        }
    ]
    assert parsed["email_sender"] == "sender@example.com"
    assert parsed["email_recipient"] == "info@turbo-don.ru"
    assert parsed["mail_datetime"] == "2026-07-03 10:00:00"
    assert parsed["process"] == "исполнение"
    assert parsed["spam"] is False
    assert parsed["confidence_level"] == ""
    assert parsed["matching_keywords"] == ""
    assert parsed["processing_notes"] == ""


def test_parse_document_xml_reads_legacy_optional_fields():
    parsed = parse_document_xml(LEGACY_XML)
    assert parsed is not None
    assert parsed["spam"] is False
    assert parsed["confidence_level"] == "ВЫСОКАЯ"
    assert parsed["matching_keywords"] == "договор; счёт"
    assert parsed["processing_notes"] == "Автоматическая регистрация разрешена."


def test_parse_document_xml_invalid_returns_none():
    assert parse_document_xml("") is None
    assert parse_document_xml("<broken") is None
    assert parse_document_xml("<other><x>1</x></other>") is None


def test_format_mail_datetime_for_xml_uses_moscow_time():
    utc = datetime(2026, 7, 22, 7, 37, 41, tzinfo=timezone.utc)
    assert _format_mail_datetime_for_xml(utc) == "2026-07-22 10:37:41"
    naive_utc = utc.replace(tzinfo=None)
    assert _format_mail_datetime_for_xml(naive_utc) == "2026-07-22 10:37:41"


def test_build_and_validate_roundtrip():
    email = EmailMessage(
        message_id="<xml@test>",
        mailbox="info@turbo-don.ru",
        sender_email="sender@example.com",
        subject="Тест",
        received_at=datetime.now(timezone.utc),
    )
    decision = RoutingDecision(
        organization="НП",
        direction="ПР",
        services=[
            ServiceRoute(
                code="00-000076",
                name="Отдел",
                direction="ПР",
            )
        ],
        confidence_level=ConfidenceLevel.HIGH,
        theme="Тест",
        partner="ООО Пример",
    )
    xml = build_xml_document(email, recipient="info@turbo-don.ru", decision=decision)
    assert validate_xml_document(xml)
    parsed = parse_document_xml(xml)
    assert parsed is not None
    assert parsed["organization"] == "НП"
    assert parsed["services"][0]["name"] == "00-000076"
    assert parsed["services"][0]["reasoning"] == "Тест"


def test_document_and_service_process_match():
    email = EmailMessage(
        message_id="<process@test>",
        mailbox="info@turbo-don.ru",
        sender_email="vendor@example.com",
        subject="Информация о сроках отгрузки - Уведомление о поставке",
        received_at=datetime.now(timezone.utc),
    )
    decision = RoutingDecision(
        process="ознакомление",
        services=[
            ServiceRoute(
                code="00-000159",
                name="Сектор сопровождения производства и продаж",
                process="ознакомление",
            )
        ],
        theme="Информация о сроках отгрузки - Уведомление о поставке",
    )
    parsed = parse_document_xml(build_xml_document(email, recipient="info@turbo-don.ru", decision=decision))
    assert parsed is not None
    assert parsed["process"] == "ознакомление"
    assert parsed["services"][0]["process"] == "ознакомление"


def test_route_email_shipment_notification_process():
    from agent_pochta.routing.process_type import infer_process_type_heuristic

    subject = "Информация о сроках отгрузки товара по счету №2/85474 - Уведомление о поставке"
    assert infer_process_type_heuristic(subject, "Уведомляем о сроках отгрузки.") == "ознакомление"

    decision = route_email(
        EmailMessage(
            message_id="<ship@test>",
            mailbox="info@turbo-don.ru",
            sender_email="info@promelec.ru",
            subject=subject,
            body_text="Уведомляем о сроках отгрузки товара.",
            received_at=datetime.now(timezone.utc),
        ),
        combined_text="Уведомляем о сроках отгрузки товара.",
        recipient="info@turbo-don.ru",
    )
    assert decision.process == "ознакомление"
    parsed = parse_document_xml(decision.xml_document)
    assert parsed is not None
    assert parsed["process"] == "ознакомление"
    assert parsed["services"][0]["process"] == "ознакомление"


def test_service_reasoning_uses_email_subject():
    email = EmailMessage(
        message_id="<reason@test>",
        mailbox="info@turbo-don.ru",
        sender_email="a@b.ru",
        subject="Re: Счёт на оплату",
        received_at=datetime.now(timezone.utc),
    )
    decision = RoutingDecision(
        services=[ServiceRoute(code="00-000002", name="Бухгалтерия")],
        theme="LLM-тема",
    )
    assert service_reasoning(email, decision) == "Счёт на оплату"
    parsed = parse_document_xml(build_xml_document(email, recipient="info@turbo-don.ru", decision=decision))
    assert parsed is not None
    assert parsed["services"][0]["reasoning"] == "Счёт на оплату"


def test_empty_services_uses_reserve_department():
    email = EmailMessage(
        message_id="<reserve@test>",
        mailbox="info@turbo-don.ru",
        sender_email="a@b.ru",
        subject="Без отдела",
        received_at=datetime.now(timezone.utc),
    )
    decision = RoutingDecision(confidence_level=ConfidenceLevel.LOW, theme="Без отдела")
    xml = build_xml_document(email, recipient="info@turbo-don.ru", decision=decision)
    assert validate_xml_document(xml)
    parsed = parse_document_xml(xml)
    assert parsed is not None
    assert parsed["services"][0]["name"] == RESERVE_DEPARTMENT_CODE


def test_validate_xml_document_rejects_missing_department():
    invalid = SAMPLE_XML.replace("00-000076", "")
    assert validate_xml_document(invalid) is False


def test_upsert_from_state_persists_xml_document():
    session = MagicMock()
    repo = EmailRepository(session)
    repo.get_by_message_id = MagicMock(return_value=None)

    email = EmailMessage(
        message_id="<xml-store@test>",
        mailbox="info@turbo-don.ru",
        sender_email="a@b.ru",
        subject="Тема",
        received_at=datetime.now(timezone.utc),
    )
    state: AgentState = {
        "email": email,
        "status": ProcessingStatus.DONE,
        "meta": {"xml_document": SAMPLE_XML},
    }

    repo.upsert_from_state(state)
    row = session.add.call_args[0][0]
    payload = json.loads(row.raw_payload_json)
    assert payload["xml_document"] == SAMPLE_XML


def test_row_to_dict_exposes_document_xml():
    row = EmailMessageRow(
        id=uuid.uuid4(),
        message_id="<xml-api@test>",
        received_at=datetime.now(timezone.utc).replace(tzinfo=None),
        mailbox="info@turbo-don.ru",
        sender_email="a@b.ru",
        raw_payload_json=json.dumps({"xml_document": SAMPLE_XML}, ensure_ascii=False),
    )
    data = _row_to_dict(row)
    assert data["xml_document"] == SAMPLE_XML
    assert data["document_xml"]["organization"] == "НП"
    assert data["document_xml"]["services"][0]["name"] == "00-000076"


def test_payload_xml_fields_missing_xml():
    row = EmailMessageRow(
        id=uuid.uuid4(),
        message_id="<no-xml@test>",
        received_at=datetime.now(timezone.utc).replace(tzinfo=None),
        mailbox="info@turbo-don.ru",
        sender_email="a@b.ru",
    )
    assert _payload_xml_fields(row) == {"xml_document": None, "document_xml": None}


def test_ensure_xml_document_for_early_spam():
    email = EmailMessage(
        message_id="<spam@test>",
        mailbox="info@turbo-don.ru",
        sender_email="spam@bad.ru",
        subject="Реклама",
        received_at=datetime.now(timezone.utc),
    )
    state: AgentState = {
        "email": email,
        "spam": SpamResult(is_spam=True, confidence=0.99, reason="Стоп-слово"),
        "status": ProcessingStatus.SPAM,
    }
    xml = ensure_xml_document(state)
    assert xml is not None
    parsed = parse_document_xml(xml)
    assert parsed is not None
    assert parsed["services"][0]["name"] == SPAM_DEPARTMENT_CODE
    assert parsed["services"][0]["reasoning"] == "Реклама"
    assert "<spam>" not in xml


def test_ensure_xml_document_uses_routing_when_present():
    email = EmailMessage(
        message_id="<route@test>",
        mailbox="info@turbo-don.ru",
        sender_email="a@b.ru",
        subject="Счёт",
        received_at=datetime.now(timezone.utc),
        routing_recipient="buh@turbo-don.ru",
    )
    state: AgentState = {
        "email": email,
        "routing": RoutingResult(
            department_id="00-000002",
            department_name="Бухгалтерия",
            confidence=0.9,
            reasoning="Счёт на оплату",
        ),
        "meta": {
            "routing_decision": {
                "organization": "НП",
                "direction": "КС",
                "confidence_level": "ВЫСОКАЯ",
                "confidence_score": 90,
                "claim": False,
            },
            "routing_recipient": "buh@turbo-don.ru",
        },
    }
    xml = ensure_xml_document(state)
    assert xml is not None
    parsed = parse_document_xml(xml)
    assert parsed is not None
    assert parsed["services"][0]["name"] == "00-000002"
    assert parsed["services"][0]["title"] == "Бухгалтерия"
    assert parsed["services"][0]["reasoning"] == "Счёт"
    assert parsed["email_recipient"] == "buh@turbo-don.ru"


def _row_with_xml(*, department_id: str, department_name: str) -> EmailMessageRow:
    received_at = datetime.now(timezone.utc).replace(tzinfo=None)
    return EmailMessageRow(
        id=uuid.uuid4(),
        message_id="<human-xml@test>",
        received_at=received_at,
        mailbox="info@turbo-don.ru",
        sender_email="vendor@example.com",
        subject="Акт сверки",
        department_id=department_id,
        department_name=department_name,
        raw_payload_json=json.dumps(
            {
                "message_id": "<human-xml@test>",
                "mailbox": "info@turbo-don.ru",
                "sender_email": "vendor@example.com",
                "subject": "Акт сверки",
                "received_at": received_at.isoformat(),
                "routing_recipient": "jurist@turbo-don.ru",
                "xml_document": SAMPLE_XML,
            },
            ensure_ascii=False,
        ),
    )


def test_rebuild_xml_document_after_human_correction():
    row = _row_with_xml(department_id="00-000002", department_name="Бухгалтерия")
    email = EmailMessage(
        message_id="<human-xml@test>",
        mailbox="info@turbo-don.ru",
        sender_email="vendor@example.com",
        subject="Акт сверки",
        received_at=datetime.now(timezone.utc),
        routing_recipient="jurist@turbo-don.ru",
    )
    xml = rebuild_xml_document_from_row(
        row,
        email,
        original_department_id="00-000076",
        original_department_name="Юридический отдел",
    )
    assert xml is not None
    parsed = parse_document_xml(xml)
    assert parsed is not None
    assert parsed["services"][0]["name"] == "00-000002"
    assert parsed["services"][0]["title"] == "Бухгалтерия"
    assert parsed["services"][0]["reasoning"] == "Акт сверки"
    assert "<processing_notes>" not in xml


def test_rebuild_xml_document_after_organization_change():
    row = _row_with_xml(department_id="00-000002", department_name="Бухгалтерия")
    payload = json.loads(row.raw_payload_json)
    payload["xml_document"] = SAMPLE_XML.replace("<organization>НП</organization>", "<organization>НП</organization>").replace(
        "<направление>ПР</направление>", "<направление>КС</направление>"
    )
    row.raw_payload_json = json.dumps(payload, ensure_ascii=False)
    email = EmailMessage(
        message_id="<human-org@test>",
        mailbox="info@turbo-don.ru",
        sender_email="vendor@example.com",
        subject="Акт сверки",
        received_at=datetime.now(timezone.utc),
        routing_recipient="jurist@turbo-don.ru",
    )
    xml = rebuild_xml_document_from_row(
        row,
        email,
        organization_override="АЛ",
    )
    assert xml is not None
    parsed = parse_document_xml(xml)
    assert parsed is not None
    assert parsed["organization"] == "АЛ"
    assert parsed["direction"] == "АЛ"


def test_rebuild_xml_document_resets_org_derived_direction():
    row = _row_with_xml(department_id="00-000002", department_name="Бухгалтерия")
    payload = json.loads(row.raw_payload_json)
    payload["xml_document"] = SAMPLE_XML.replace("<organization>НП</organization>", "<organization>АЛ</organization>").replace(
        "<направление>ПР</направление>", "<направление>АЛ</направление>"
    )
    row.raw_payload_json = json.dumps(payload, ensure_ascii=False)
    email = EmailMessage(
        message_id="<human-org-reset@test>",
        mailbox="info@turbo-don.ru",
        sender_email="vendor@example.com",
        subject="Акт сверки",
        received_at=datetime.now(timezone.utc),
        routing_recipient="jurist@turbo-don.ru",
    )
    xml = rebuild_xml_document_from_row(
        row,
        email,
        organization_override="НП",
    )
    assert xml is not None
    parsed = parse_document_xml(xml)
    assert parsed is not None
    assert parsed["organization"] == "НП"
    assert parsed["direction"] == "ПР"


def test_repository_clear_xml_document():
    row = _row_with_xml(department_id="00-000076", department_name="Юридический отдел")
    repo = EmailRepository(MagicMock())
    repo.clear_xml_document(row)
    payload = json.loads(row.raw_payload_json)
    assert "xml_document" not in payload


def test_repository_rebuild_xml_after_human_correction():
    row = _row_with_xml(department_id="00-000109", department_name="Отдел закупок")
    email = EmailMessage(
        message_id="<human-xml@test>",
        mailbox="info@turbo-don.ru",
        sender_email="vendor@example.com",
        subject="Акт сверки",
        received_at=datetime.now(timezone.utc),
        routing_recipient="jurist@turbo-don.ru",
    )
    repo = EmailRepository(MagicMock())
    xml = repo.rebuild_xml_after_human_correction(
        row,
        email,
        original_department_id="00-000076",
        original_department_name="Юридический отдел",
    )
    assert xml is not None
    payload = json.loads(row.raw_payload_json)
    assert payload["xml_document"] == xml
    parsed = parse_document_xml(xml)
    assert parsed is not None
    assert parsed["services"][0]["name"] == "00-000109"


def test_service_name_is_department_code_not_title():
    email = EmailMessage(
        message_id="<code@test>",
        mailbox="info@turbo-don.ru",
        sender_email="a@b.ru",
        subject="Тест",
        received_at=datetime.now(timezone.utc),
    )
    decision = RoutingDecision(
        services=[
            ServiceRoute(
                code="00-000076",
                name="ОРКК / ПАО Газпром",
            )
        ],
        confidence_level=ConfidenceLevel.HIGH,
        theme="Тест",
    )
    parsed = parse_document_xml(build_xml_document(email, recipient="info@turbo-don.ru", decision=decision))
    assert parsed is not None
    assert parsed["services"][0]["name"] == "00-000076"
    assert parsed["services"][0]["title"] == "ОРКК / ПАО Газпром"
    assert parsed["services"][0]["name"] != "ОРКК / ПАО Газпром"


def test_partner_dash_when_missing():
    email = EmailMessage(
        message_id="<partner@test>",
        mailbox="info@turbo-don.ru",
        sender_email="a@b.ru",
        subject="Тест",
        received_at=datetime.now(timezone.utc),
    )
    decision = RoutingDecision(confidence_level=ConfidenceLevel.LOW, theme="Тест")
    parsed = parse_document_xml(build_xml_document(email, recipient="info@turbo-don.ru", decision=decision))
    assert parsed is not None
    assert parsed["partner"] == "-"
    assert format_partner(None) == "-"
    assert parsed["services"][0]["name"] == RESERVE_DEPARTMENT_CODE


def test_matching_keywords_exclude_internal_rule_names():
    decision = route_email(
        EmailMessage(
            message_id="<kw@test>",
            mailbox="info@turbo-don.ru",
            sender_email="a@b.ru",
            subject="Претензия по договору",
            body_text="Направляем претензию",
            received_at=datetime.now(timezone.utc),
        ),
        combined_text="Направляем претензию",
        recipient="jurist@turbo-don.ru",
    )
    assert decision.xml_document
    parsed = parse_document_xml(decision.xml_document)
    assert parsed is not None
    keywords = decision.matching_keywords
    assert "email_keyword" not in keywords
    assert "content" not in keywords
    assert "jurist" in keywords
    assert "<matching_keywords>" not in decision.xml_document


def test_claim_false_positive_on_risk_and_exception():
    assert contains_claim_marker("Оценка риска поставки без претензий") is False
    assert contains_claim_marker("Просим исключение из списка") is False
    assert contains_claim_marker("Направляем претензию и готовим иск") is True


def test_sanitize_theme_strips_forbidden_tags():
    assert sanitize_theme("  <think>hidden</think>Re: Счёт  ") == "Счёт"
    assert sanitize_theme("") == "Без темы"


def test_build_stub_xml_theme_format():
    theme = build_stub_xml_theme(
        "Распиновка кабеля",
        "Необходимо предоставить распиновку кабеля и контакт специалиста.",
    )
    assert " - " in theme
    assert "распиновк" in theme.lower()
    assert theme.startswith("Необходимо")


def test_normalize_xml_theme_adds_key_phrase():
    theme = normalize_xml_theme(
        "Необходимо предоставить распиновку кабеля",
        subject="Распиновка кабеля",
    )
    assert theme.startswith("Запрос:")
    assert "Распиновка кабеля" in theme


def test_xml_theme_uses_action_prefix_not_llm_description():
    email = EmailMessage(
        message_id="<theme@test>",
        mailbox="info@turbo-don.ru",
        sender_email="client@example.ru",
        subject="Распиновка кабеля",
        received_at=datetime.now(timezone.utc),
    )
    llm_theme = (
        "Необходимо предоставить распиновку кабеля или контакт специалиста, "
        "а также актуальную информацию о сроке мероприятия - "
        "Запрос на предоставление распиновки кабеля или контакта специалиста"
    )
    decision = RoutingDecision(
        services=[ServiceRoute(code="00-000076", name="ОРКК")],
        confidence_level=ConfidenceLevel.HIGH,
        theme=llm_theme,
    )
    parsed = parse_document_xml(build_xml_document(email, recipient="info@turbo-don.ru", decision=decision))
    assert parsed is not None
    assert parsed["theme"] == "Запрос: Распиновка кабеля"
    assert parsed["services"][0]["reasoning"] == "Распиновка кабеля"


def test_email_subject_theme_ignores_body_text():
    email = EmailMessage(
        message_id="<ol@test>",
        mailbox="info@turbo-don.ru",
        sender_email="vendor@lan-service.ru",
        subject="ОЛ 31222, 31240 в работу",
        body_text="Добрый день!ОЛ 31222, 31340 отправлены в просчет.",
        received_at=datetime.now(timezone.utc),
    )
    assert email_subject_theme(email) == "Отправить в просчёт: ОЛ 31222, 31240 в работу"
    assert build_subject_xml_theme(email.subject or "") == "Запрос: ОЛ 31222, 31240 в работу"


def test_build_subject_xml_theme_action_prefixes():
    assert build_subject_xml_theme("Заказ") == "Запрос: Заказ"
    assert build_subject_xml_theme("ОЛ 31222, 31240 в работу") == "Запрос: ОЛ 31222, 31240 в работу"
    assert build_subject_xml_theme("Счёт") == "Запрос: Счёт"
    assert build_subject_xml_theme("RE: неподписанные УПД") == "Проверить: неподписанные УПД"
    assert (
        build_subject_xml_theme(
            "ОЛ 31222 в работу",
            combined_text="ОЛ отправлены в просчет.",
        )
        == "Отправить в просчёт: ОЛ 31222 в работу"
    )


def test_lan_service_xml_partner_from_signature():
    body = (
        "Добрый день! ОЛ 31222, 31340 отправлены в просчет.\n\n"
        "С уважением,\n"
        "Менеджер\n"
        "ООО ЛАН-Сервис"
    )
    email = EmailMessage(
        message_id="<lan@test>",
        mailbox="info@turbo-don.ru",
        sender_email="sales@lan-service.ru",
        sender_name="Lan Service",
        subject="ОЛ 31222, 31240 в работу",
        body_text=body,
        received_at=datetime.now(timezone.utc),
    )
    decision = RoutingDecision(
        services=[ServiceRoute(code="00-000076", name="ОРКК")],
        confidence_level=ConfidenceLevel.HIGH,
        theme="ОЛ 31222, 31240 в работу",
        partner="ООО ЛАН-Сервис",
    )
    parsed = parse_document_xml(build_xml_document(email, recipient="info@turbo-don.ru", decision=decision))
    assert parsed is not None
    assert parsed["theme"] == "Запрос: ОЛ 31222, 31240 в работу"
    assert parsed["partner"] == "ООО ЛАН-Сервис"
    assert parsed["partner"] != "Lan Service"


def test_human_correction_preserves_xml_theme():
    llm_theme = (
        "Необходимо предоставить распиновку кабеля - "
        "Запрос на предоставление распиновки кабеля"
    )
    row = EmailMessageRow(
        id=uuid.uuid4(),
        message_id="<theme-human@test>",
        received_at=datetime.now(timezone.utc).replace(tzinfo=None),
        mailbox="info@turbo-don.ru",
        sender_email="vendor@example.com",
        subject="Re: Распиновка",
        department_id="00-000002",
        department_name="Бухгалтерия",
        raw_payload_json=json.dumps(
            {
                "routing_recipient": "info@turbo-don.ru",
                "xml_document": SAMPLE_XML.replace("Тестовая тема", llm_theme),
            },
            ensure_ascii=False,
        ),
    )
    email = EmailMessage(
        message_id="<theme-human@test>",
        mailbox="info@turbo-don.ru",
        sender_email="vendor@example.com",
        subject="Re: Распиновка",
        received_at=datetime.now(timezone.utc),
    )
    xml = rebuild_xml_document_from_row(
        row,
        email,
        original_department_id="00-000076",
        original_department_name="Юридический отдел",
    )
    assert xml is not None
    parsed = parse_document_xml(xml)
    assert parsed is not None
    assert parsed["theme"] == "Запрос: Распиновка"


def test_keyword_in_text_avoids_risk_and_exception():
    from agent_pochta.routing.normalize import keyword_in_text

    assert keyword_in_text("иск", "оценка риска поставки") is False
    assert keyword_in_text("иск", "просим исключить позицию") is False
    assert keyword_in_text("иск", "направляем иск в суд") is True


def test_routing_risk_email_no_spurious_isk_keyword():
    decision = route_email(
        EmailMessage(
            message_id="<risk@test>",
            mailbox="info@turbo-don.ru",
            sender_email="client@example.ru",
            subject="Оценка риска поставки",
            body_text="Просим исключить позицию из спецификации",
            received_at=datetime.now(timezone.utc),
            routing_recipient="jurist@turbo-don.ru",
        ),
        combined_text="Просим исключить позицию из спецификации",
        recipient="jurist@turbo-don.ru",
    )
    parsed = parse_document_xml(decision.xml_document)
    assert parsed is not None
    assert parsed["claim"] is False
    assert "иск" not in decision.matching_keywords
    assert parsed["services"][0]["name"] == "00-000044"


def test_tz_example_xml_shape():
    email = EmailMessage(
        message_id="<tz@test>",
        mailbox="info@turbo-don.ru",
        sender_email="client@example.ru",
        subject="Запрос ТКП на промышленный расходомер",
        received_at=datetime(2026, 6, 29, 10, 15, tzinfo=timezone.utc),
        routing_recipient="td_asutp@turbo-don.ru",
    )
    decision = RoutingDecision(
        organization="НП",
        direction="ПР",
        services=[
            ServiceRoute(
                code="00-000076",
                name="ОРКК",
            )
        ],
        confidence_level=ConfidenceLevel.HIGH,
        matching_keywords=["td_asutp", "промышленное оборудование", "Газпром"],
        partner="ООО Пример",
        theme="Запрос ТКП на промышленный расходомер",
    )
    xml = build_xml_document(email, recipient="td_asutp@turbo-don.ru", decision=decision)
    assert validate_xml_document(xml)
    parsed = parse_document_xml(xml)
    assert parsed is not None
    assert parsed["organization"] == "НП"
    assert parsed["direction"] == "ПР"
    assert parsed["services"][0]["name"] == "00-000076"
    assert parsed["services"][0]["reasoning"] == "Запрос ТКП на промышленный расходомер"
    assert "<spam>" not in xml
    assert "<confidence_level>" not in xml
    assert "<matching_keywords>" not in xml
    assert "<processing_notes>" not in xml
