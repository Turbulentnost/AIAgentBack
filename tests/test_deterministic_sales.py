"""Жёсткая маршрутизация продуктовых и sales-писем до LLM."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agent_pochta.nodes.n5_route_department import _needs_rag_fallback
from agent_pochta.routing import RouteEngine, route_email
from agent_pochta.routing.models import ConfidenceLevel
from agent_pochta.schemas import EmailMessage
from agent_pochta.services.routing_departments import (
    build_departments_from_rules,
    load_routing_rules,
)


@pytest.fixture
def engine() -> RouteEngine:
    return RouteEngine.load()


def _email(**overrides: str) -> EmailMessage:
    values = {
        "message_id": "<deterministic-sales@example>",
        "mailbox": "sales@turbo-don.ru",
        "routing_recipient": "sales@turbo-don.ru",
        "sender_email": "client@example.ru",
        "subject": "Запрос",
        "body_text": "",
        "received_at": datetime.now(timezone.utc),
    }
    values.update(overrides)
    return EmailMessage(**values)


def _route(engine: RouteEngine, text: str, **email_overrides: str):
    email = _email(**email_overrides)
    return route_email(email, combined_text=text, engine=engine)


def test_bmi_routes_to_bmi_organization(engine):
    decision = _route(engine, "Нужен BMI для нового объекта.")

    assert decision.services[0].code == "00-000128"
    assert decision.organization == "БМ"
    assert decision.direction == "БМ"
    assert decision.match_source == "det_product_bmi"


def test_bytovye_routes_to_otp(engine):
    decision = _route(engine, "Интересуют бытовые приборы учёта.")

    assert decision.services[0].code == "00-000099"
    assert decision.match_source == "det_product_bytovye_otp"


def test_spu_routes_to_ope(engine):
    decision = _route(engine, "Просим КП на СПУ.")

    assert decision.services[0].code == "00-000074"
    assert decision.match_source == "det_product_spu_ope"


def test_service_routes_to_service_department(engine):
    decision = _route(engine, "Требуется сервисная служба для обслуживания расходомера.")

    assert decision.services[0].code == "00-000104"
    assert decision.match_source == "det_product_service"


def test_foreign_domain_in_body_routes_to_ved(engine):
    decision = _route(
        engine,
        "Запрос КП. Контакт: sales@partner-export.de",
        sender_email="client@example.ru",
    )

    assert decision.services[0].code == "00-000015"
    assert decision.match_source == "det_foreign_domain"


def test_export_keyword_without_foreign_domain_does_not_route_to_ved(engine):
    decision = _route(engine, "Запрос на export оборудования в Казахстан.")

    assert decision.services[0].code != "00-000015"
    assert decision.match_source != "det_sales_foreign"


def test_com_sender_with_sales_context_routes_to_ved(engine):
    decision = _route(
        engine,
        "Запрос КП на оборудование.",
        sender_email="info@hebei-export.com",
    )

    assert decision.services[0].code == "00-000015"
    assert decision.match_source in {"det_sales_foreign", "det_foreign_domain"}


def test_foreign_sender_routes_to_ved_without_sales_context(engine):
    decision = _route(
        engine,
        "Please send quotation for spare parts.",
        sender_email="wy003@weiyemachinery.cn",
        mailbox="uk_omto10@turbo-don.ru",
        routing_recipient="uk_omto10@turbo-don.ru",
    )

    assert decision.services[0].code == "00-000015"
    assert decision.match_source == "det_foreign_domain"


def test_foreign_domain_in_cc_routes_to_ved(engine):
    email = _email(
        sender_email="client@example.ru",
        mailbox="info@turbo-don.ru",
        routing_recipient="info@turbo-don.ru",
    )
    email = email.model_copy(update={"cc": ["sales@partner-export.de"]})
    decision = route_email(
        email,
        combined_text="Forwarding supplier request.",
        recipient="info@turbo-don.ru",
        engine=engine,
    )

    assert decision.services[0].code == "00-000015"
    assert decision.match_source == "det_foreign_domain"


def test_gmail_com_is_not_foreign(engine):
    decision = _route(
        engine,
        "Запрос КП на оборудование.",
        sender_email="user@gmail.com",
    )

    assert decision.services[0].code != "00-000015"
    assert decision.match_source != "det_foreign_domain"


def test_ru_domain_in_url_does_not_route_to_ved(engine):
    decision = _route(
        engine,
        "Счёт на поставку: https://turbo-don.ru/price",
        sender_email="client@example.ru",
    )

    assert decision.services[0].code != "00-000015"
    assert decision.match_source != "det_sales_foreign"


def test_domestic_invoice_keywords_do_not_route_to_ved(engine):
    decision = _route(
        engine,
        "Счет на поставку, сообщите стоимость.",
        sender_email="client@example.ru",
    )

    assert decision.services[0].code != "00-000015"
    assert decision.match_source != "det_sales_foreign"


@pytest.mark.parametrize("marker", ["Гранд", "UFG-H"])
def test_dealer_markers_route_to_odp(engine, marker):
    decision = _route(engine, f"Запрос КП на {marker}.")

    assert decision.services[0].code == "00-000155"
    assert decision.match_source == "det_sales_dealer"


def test_gazprom_routes_to_opg(engine):
    decision = _route(engine, "ПАО Газпром направляет запрос на поставку расходомеров.")

    assert decision.services[0].code == "00-000076"
    assert decision.match_source == "det_sales_gazprom"


def test_chairman_marker_overrides_gazprom_on_info(engine):
    decision = _route(
        engine,
        "ПАО Газпром направляет письмо председателю Совета Директоров.",
        mailbox="info@turbo-don.ru",
        routing_recipient="info@turbo-don.ru",
    )

    assert decision.services[0].code == "00-000001"
    assert decision.direction == "КС"
    assert decision.match_source in {"det_chairman", "info_strict"}


def test_chairman_marker_not_routed_off_info(engine):
    decision = _route(
        engine,
        "ПАО Газпром направляет письмо председателю Совета Директоров.",
    )

    assert decision.services[0].code != "00-000001"
    assert decision.match_source != "det_chairman"


def test_gazprom_igor_borisovich_routes_to_chairman_on_info(engine):
    decision = _route(
        engine,
        "ПАО Газпром направляет материалы для Игорь Борисович.",
        mailbox="info@turbo-don.ru",
        routing_recipient="info@turbo-don.ru",
    )

    assert decision.services[0].code == "00-000001"
    assert decision.direction == "КС"
    assert decision.match_source in {"det_chairman", "info_strict"}


def test_predsedatel_igor_borisovich_routes_to_chairman_on_info(engine):
    decision = _route(
        engine,
        "Председателю Совета Директоров Игорь Борисович — материалы для рассмотрения.",
        mailbox="info@turbo-don.ru",
        routing_recipient="info@turbo-don.ru",
    )

    assert decision.services[0].code == "00-000001"
    assert decision.direction == "КС"
    assert decision.match_source in {"det_chairman", "info_strict"}


@pytest.mark.parametrize("holding", ["СИБУР", "Роснефть"])
def test_orkk_holdings_route_to_key_accounts(engine, holding):
    decision = _route(engine, f"{holding}: запрос КП на промышленное оборудование.")

    assert decision.services[0].code == "00-000042"
    assert decision.match_source == "det_sales_orkk"


def test_industrial_without_holding_routes_to_key_accounts(engine):
    decision = _route(engine, "Запрос КП на расходомер для промышленного газопровода.")

    assert decision.services[0].code == "00-000042"
    assert decision.match_source == "det_sales_industrial"


def test_attachment_text_in_combined_text_triggers_product_rule(engine):
    decision = _route(
        engine,
        "См. вложение.\n\n=== ВЛОЖЕНИЯ (1) — извлечённый текст ===\nBMI",
    )

    assert decision.services[0].code == "00-000128"
    assert decision.match_source == "det_product_bmi"
    assert decision.confidence_level == ConfidenceLevel.HIGH
    assert not _needs_rag_fallback(decision)


def test_deterministic_keywords_are_available_for_qdrant_department_sync():
    departments = {
        department.department_id: department
        for department in build_departments_from_rules(load_routing_rules())
    }

    assert "сибур" in departments["00-000042"].keywords
    assert "сервисная служба" in departments["00-000104"].keywords
    assert "ufg-h" in departments["00-000155"].keywords
