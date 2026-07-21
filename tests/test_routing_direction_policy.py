"""Политика направления: КС только для неясных, явные запросы → ПР; оборудование БМИ → БМ."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agent_pochta.routing import RouteEngine, route_email
from agent_pochta.routing.organizations import (
    DIRECTION_DEFAULT,
    DIRECTION_UNCLEAR,
    direction_for_organization_override,
)
from agent_pochta.services.odata_incoming_mapper import resolve_payer_direction
from agent_pochta.schemas import EmailMessage


def _email(**kw) -> EmailMessage:
    base = dict(
        message_id="<dir-policy@example>",
        mailbox="info@turbo-don.ru",
        sender_email="client@example.ru",
        subject="",
        body_text="",
        received_at=datetime.now(timezone.utc),
        routing_recipient="info@turbo-don.ru",
    )
    base.update(kw)
    return EmailMessage(**base)


@pytest.fixture
def engine() -> RouteEngine:
    return RouteEngine.load()


@pytest.mark.parametrize(
    "keyword",
    [
        "ПУРГ",
        "ПУРГС",
        "ГРПШ",
        "ГРПБ",
        "ШУРГ",
        "ПУГС",
        "ПУГ",
        "ГИС",
        "ГРС",
        "АГНКС",
        "СИРГ",
        "СИГК",
        "АГРС",
        "УИРГ",
        "УКИРГ",
        "блок-бокс",
        "пункт учета",
        "шкафной",
        "УРМЦ",
    ],
)
def test_bmi_equipment_routes_to_bm_direction(engine, keyword: str) -> None:
    decision = route_email(
        _email(subject=keyword),
        combined_text=f"Запрос по оборудованию {keyword}",
        recipient="info@turbo-don.ru",
        engine=engine,
    )
    assert decision.direction == "БМ"
    assert decision.organization == "НП"
    assert decision.services[0].code == "00-000163"
    assert decision.match_source.startswith("det_product_")


def test_bmi_equipment_commercial_routes_to_sales(engine) -> None:
    decision = route_email(
        _email(subject="ТКП на ПУРГ"),
        combined_text="Просим направить коммерческое предложение на шкаф ПУРГ, счет во вложении",
        recipient="info@turbo-don.ru",
        engine=engine,
    )
    assert decision.direction == "БМ"
    assert decision.services[0].code == "00-000128"


def test_unclear_info_mailbox_stays_ks(engine) -> None:
    decision = route_email(
        _email(subject="."),
        combined_text="",
        recipient="info@turbo-don.ru",
        engine=engine,
    )
    assert decision.direction == DIRECTION_UNCLEAR
    assert decision.match_source == "info_strict_unclear"


def test_clear_hr_request_uses_pr(engine) -> None:
    decision = route_email(
        _email(subject="Практика"),
        combined_text="Запрос на прохождение практики в компании",
        recipient="info@turbo-don.ru",
        engine=engine,
    )
    assert decision.services[0].code == "00-000063"
    assert decision.direction == DIRECTION_DEFAULT


def test_tender_uses_pr_direction(engine) -> None:
    decision = route_email(
        _email(subject="Тендер"),
        combined_text="Закупочная процедура",
        recipient="tender@turbo-don.ru",
        engine=engine,
    )
    assert decision.direction == DIRECTION_DEFAULT


def test_resolve_payer_direction_np_default_is_production() -> None:
    assert resolve_payer_direction("НП", None) == "ТурбулентностьДОНПроизводство1"
    assert resolve_payer_direction("НП", "КС") == "ТурбулентностьДОНКС"
    assert resolve_payer_direction("НП", "ПР") == "ТурбулентностьДОНПроизводство1"
    assert resolve_payer_direction("БМ", "БМ") == "БМИ"


def test_direction_for_organization_override_resets_to_pr() -> None:
    assert (
        direction_for_organization_override(
            "НП",
            existing_direction="АЛ",
            previous_organization="АЛ",
        )
        == DIRECTION_DEFAULT
    )


def test_rebuild_decision_xml_updates_commercial_direction(engine) -> None:
    from agent_pochta.routing.engine import rebuild_decision_xml

    decision = route_email(
        _email(subject="."),
        combined_text="",
        recipient="info@turbo-don.ru",
        engine=engine,
    )
    assert decision.direction == DIRECTION_UNCLEAR
    updated = rebuild_decision_xml(
        _email(subject="Тендер"),
        decision,
        recipient="info@turbo-don.ru",
        department_id="00-000054",
        department_name="Отдел тендерных продаж",
    )
    assert updated.services[0].code == "00-000054"
    assert updated.direction == DIRECTION_DEFAULT
    assert "<направление>ПР</направление>" in (updated.xml_document or "")


@pytest.mark.parametrize(
    "department_id",
    ["00-000054", "00-000042", "00-000076", "00-000155"],
)
def test_commercial_departments_use_pr_direction(engine, department_id: str) -> None:
    from agent_pochta.routing.organizations import resolve_direction_for_department

    assert (
        resolve_direction_for_department(
            department_id,
            "НП",
            rules=engine.rules,
            fallback_direction=DIRECTION_UNCLEAR,
        )
        == DIRECTION_DEFAULT
    )
