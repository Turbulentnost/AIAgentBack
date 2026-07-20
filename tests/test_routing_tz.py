"""Тесты детерминированной маршрутизации по ТЗ §19 (T-01…T-15)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agent_pochta.graph import build_graph
from agent_pochta.routing import RouteEngine, route_email
from agent_pochta.routing.models import ConfidenceLevel
from agent_pochta.routing.recipients import routing_message_id, split_routing_recipients
from agent_pochta.routing.xml_builder import validate_xml_document
from agent_pochta.schemas import EmailMessage, ProcessingStatus


def _email(**kw) -> EmailMessage:
    base = dict(
        message_id="<t@example>",
        mailbox="info@turbo-don.ru",
        sender_email="client@example.ru",
        subject="",
        body_text="",
        received_at=datetime.now(timezone.utc),
    )
    base.update(kw)
    return EmailMessage(**base)


@pytest.fixture
def engine():
    return RouteEngine.load()


def test_t01_exact_tender_email(engine):
    decision = route_email(
        _email(subject="Тендер"),
        combined_text="Закупочная процедура",
        recipient="tender@turbo-don.ru",
        engine=engine,
    )
    assert decision.services[0].code == "00-000054"
    assert decision.direction == "КС"
    assert decision.confidence_level in {ConfidenceLevel.HIGH, ConfidenceLevel.MEDIUM}
    assert decision.xml_document
    assert validate_xml_document(decision.xml_document)


def test_t02_email_keyword_omto(engine):
    decision = route_email(
        _email(),
        combined_text="Поставка материалов",
        recipient="unknown_omto@turbo-don.ru",
        engine=engine,
    )
    assert decision.services[0].code == "00-000065"


def test_t03_info_mailbox_tkp_by_content(engine):
    decision = route_email(
        _email(subject="ТКП на UFG"),
        combined_text="Просим подготовить технико-коммерческое предложение на UFG",
        recipient="info@turbo-don.ru",
        engine=engine,
    )
    assert decision.services[0].code != "00-000066"
    assert decision.match_source in {"content", "det_sales_industrial", "sales_odp", "sales_orkk"}


def test_t04_noreply_spam():
    app = build_graph()
    res = app.invoke(
        {
            "email": _email(
                message_id="<noreply@example>",
                sender_email="no-reply@service.example",
                subject="Автоматическое уведомление",
                body_text="Ваш запрос получен.",
            )
        }
    )
    assert res["status"] == ProcessingStatus.SPAM


def test_t06_accounting_act(engine):
    decision = route_email(
        _email(subject="Акт сверки"),
        combined_text="Направляем акт сверки за квартал",
        recipient="td_buh3@turbo-don.ru",
        engine=engine,
    )
    assert decision.services[0].code == "00-000002"


def test_exact_email_officemanager_from_tz(engine):
    decision = route_email(
        _email(subject="Документы"),
        combined_text="Просьба зарегистрировать входящее.",
        recipient="officemanager@turbo-don.ru",
        engine=engine,
    )
    assert decision.services[0].code == "00-000066"
    assert decision.match_source == "exact_email"


def test_t07_legal_claim(engine):
    decision = route_email(
        _email(subject="Претензия"),
        combined_text="Направляем претензию по договору, готовим иск",
        recipient="info@turbo-don.ru",
        engine=engine,
    )
    assert decision.services[0].code == "00-000044"
    assert decision.claim is True


def test_t08_service_repair(engine):
    decision = route_email(
        _email(subject="Ремонт расходомера"),
        combined_text="Требуется ремонт промышленного оборудования",
        recipient="info@turbo-don.ru",
        engine=engine,
    )
    assert decision.services[0].code == "00-000163"
    assert decision.direction == "СС"


def test_t09_grand_spi_organization(engine):
    decision = route_email(
        _email(subject="Гранд SPI"),
        combined_text="Рекламация на бытовой счетчик Гранд SPI",
        recipient="info@turbo-don.ru",
        engine=engine,
    )
    assert decision.organization == "АЛ"


def test_t10_bmi_direction(engine):
    decision = route_email(
        _email(subject="ПУРГ"),
        combined_text="Запрос на измерительный шкаф ПУРГ",
        recipient="info@turbo-don.ru",
        engine=engine,
    )
    assert decision.direction == "БМ"
    assert decision.services[0].code == "00-000163"


def test_t11_orkk_holding(engine):
    decision = route_email(
        _email(subject="Запрос"),
        combined_text="Для нужд ПАО Лукойл просим ТКП",
        recipient="info@turbo-don.ru",
        engine=engine,
    )
    assert decision.services[0].code == "00-000042"


def test_t13_multiple_recipients():
    email = _email(
        to=["tender@turbo-don.ru", "jurist@turbo-don.ru"],
        cc=["info@turbo-don.ru"],
    )
    recipients = split_routing_recipients(email)
    assert recipients == ["tender@turbo-don.ru", "jurist@turbo-don.ru"]
    assert "info@turbo-don.ru" not in recipients
    assert routing_message_id(email.message_id, "tender@turbo-don.ru") != email.message_id


def test_split_routing_recipients_ignores_cc():
    email = _email(
        to=["tender@turbo-don.ru"],
        cc=["jurist@turbo-don.ru", "info@turbo-don.ru"],
    )
    assert split_routing_recipients(email) == ["tender@turbo-don.ru"]


def test_split_routing_recipients_to_only_when_cc_duplicates_to():
    email = _email(
        to=["tender@turbo-don.ru"],
        cc=["tender@turbo-don.ru"],
    )
    assert split_routing_recipients(email) == ["tender@turbo-don.ru"]


def test_t14_low_confidence_reserve(engine):
    decision = route_email(
        _email(subject="Привет"),
        combined_text="",
        recipient="unknown@external.ru",
        engine=engine,
    )
    assert decision.services[0].code == "00-000066"
    assert decision.confidence_level == ConfidenceLevel.LOW


def test_xml_always_valid(engine):
    decision = route_email(
        _email(subject="Тест"),
        combined_text="Проверка XML",
        recipient="tender@turbo-don.ru",
        engine=engine,
    )
    assert decision.xml_document
    assert validate_xml_document(decision.xml_document)
    assert "<think>" not in decision.xml_document
