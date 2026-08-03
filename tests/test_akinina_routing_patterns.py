"""Паттерны маршрутизации по анализу Акининой (OTP / ТКП / сервис / выставки / суд)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agent_pochta.routing import RouteEngine, route_email
from agent_pochta.routing.deterministic_sales import reset_deterministic_sales_rules_cache
from agent_pochta.schemas import EmailMessage


@pytest.fixture
def engine() -> RouteEngine:
    reset_deterministic_sales_rules_cache()
    return RouteEngine.load()


def _email(**overrides) -> EmailMessage:
    values = {
        "message_id": "<akinina-patterns@example>",
        "mailbox": "info@turbo-don.ru",
        "routing_recipient": "info@turbo-don.ru",
        "sender_email": "client@example.ru",
        "subject": "Запрос",
        "body_text": "",
        "received_at": datetime.now(timezone.utc),
    }
    values.update(overrides)
    return EmailMessage(**values)


def _route(engine: RouteEngine, text: str, **email_overrides):
    email = _email(**email_overrides)
    return route_email(email, combined_text=text, engine=engine)


@pytest.mark.parametrize(
    "text",
    [
        "Просим предоставить дубликат паспорта",
        "Нужно восстановить паспорт счётчика",
        "Села батарея, не работает индикация",
        "Пропала индикация на табло",
        "Дубликат паспорта счетчика газа Гранд",
    ],
)
def test_otp_passport_battery_routes_to_099(engine, text):
    decision = _route(engine, text)
    assert decision.services[0].code == "00-000099"
    assert decision.organization == "АЛ"


@pytest.mark.parametrize(
    "text",
    [
        "Просим подготовить ТКП на расходомеры",
        "Просим выслать КП по запросу",
        "Просим рассмотреть возможность поставки оборудования",
        "Уточните цену и наличие на складе",
        "Заявка на проработку поставки",
    ],
)
def test_tkp_supply_routes_to_042(engine, text):
    decision = _route(engine, text)
    assert decision.services[0].code == "00-000042"
    assert decision.services[0].code != "00-000001"
    assert decision.services[0].code != "00-000066"
    assert decision.services[0].code != "00-000152"


def test_gazprom_tkp_not_forced_to_chairman(engine):
    decision = _route(
        engine,
        "ПАО Газпром: просим подготовить ТКП и выслать КП",
        sender_email="notify@office.gazprom.ru",
    )
    assert decision.services[0].code == "00-000042"
    assert decision.match_source != "info_strict"


def test_gazprom_verification_not_forced_to_chairman(engine):
    decision = _route(
        engine,
        "ПАО Газпром направляет документы на поверку средств измерений",
        sender_email="notify@office.gazprom.ru",
    )
    assert decision.services[0].code == "00-000025"
    assert decision.match_source != "info_strict"


def test_exhibition_routes_to_013(engine):
    decision = _route(
        engine,
        "Приглашение на нефтегазовую выставку в Казахстане",
    )
    assert decision.services[0].code == "00-000013"


def test_court_cassation_routes_to_044(engine):
    decision = _route(
        engine,
        "Направляем кассационную жалобу и апелляционное определение суда",
    )
    assert decision.services[0].code == "00-000044"


@pytest.mark.parametrize(
    "text",
    [
        "Акт несоответствия продукции",
        "О несоответствии комплектации",
        "Недокомплект по поставке",
        "Акт ВК входного контроля",
        "Запрос ЗИП на расходомер",
    ],
)
def test_service_mismatch_zip_routes_to_163(engine, text):
    decision = _route(engine, text)
    assert decision.services[0].code == "00-000163"
    assert decision.direction == "СС"


def test_dealer_grand_kp_still_routes_to_155(engine):
    decision = _route(engine, "Запрос КП на Гранд.")
    assert decision.services[0].code == "00-000155"
    assert decision.match_source == "det_sales_dealer"
