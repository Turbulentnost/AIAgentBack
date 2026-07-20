"""Строгие правила маршрутизации только для info@turbo-don.ru."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agent_pochta.nodes.n5_route_department import _needs_rag_fallback
from agent_pochta.routing import RouteEngine, route_email
from agent_pochta.routing.models import ConfidenceLevel
from agent_pochta.schemas import EmailMessage


def _email(**kw) -> EmailMessage:
    base = dict(
        message_id="<info-strict@example>",
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
def engine():
    return RouteEngine.load()


def test_info_amural_routes_to_chairman(engine):
    decision = route_email(
        _email(subject="Обращение", sender_email="secretary@region.gov.ru"),
        combined_text="Письмо на имя Амураль Игорь Борисович",
        recipient="info@turbo-don.ru",
        engine=engine,
    )
    assert decision.services[0].code == "00-000001"
    assert decision.direction == "КС"
    assert decision.match_source == "info_strict"
    assert decision.confidence_level == ConfidenceLevel.HIGH
    assert not _needs_rag_fallback(decision)


def test_info_amural_in_attachments_text(engine):
    decision = route_email(
        _email(subject="Документы"),
        combined_text=(
            "См. вложение.\n\n=== ВЛОЖЕНИЯ ===\nИП Амураль И.Б., приглашение"
        ),
        recipient="INFO@TURBO-DON.RU",
        engine=engine,
    )
    assert decision.services[0].code == "00-000001"
    assert decision.match_source == "info_strict"


def test_info_gazprom_company_routes_to_chairman(engine):
    decision = route_email(
        _email(
            subject="Приглашение",
            sender_email="notify@office.gazprom.ru",
        ),
        combined_text="Официальное приглашение от ПАО Газпром на совещание",
        recipient="info@turbo-don.ru",
        engine=engine,
    )
    assert decision.services[0].code == "00-000001"
    assert decision.match_source == "info_strict"
    assert decision.confidence_level == ConfidenceLevel.HIGH


def test_info_vodokanal_routes_to_chairman(engine):
    decision = route_email(
        _email(subject="Запрос"),
        combined_text="МП Водоканал направляет документы на согласование",
        recipient="info@turbo-don.ru",
        engine=engine,
    )
    assert decision.services[0].code == "00-000001"
    assert decision.match_source == "info_strict"


def test_info_gas_theme_alone_not_forced_to_ud(engine):
    """Тема «газ» без признаков ПАО Газпром не должна срабатывать как rule 1."""
    decision = route_email(
        _email(subject="Вопрос по газу", sender_email="buyer@local-dealer.ru"),
        combined_text="Нужна консультация по газовому учёту бытового объекта",
        recipient="info@turbo-don.ru",
        engine=engine,
    )
    assert decision.match_source != "info_strict"


def test_info_ministry_routes_to_operational_director(engine):
    decision = route_email(
        _email(
            subject="Исх. письмо",
            sender_email="office@minstroy-region.gov.ru",
        ),
        combined_text="Министерство строительства направляет запрос на согласование",
        recipient="info@turbo-don.ru",
        engine=engine,
    )
    assert decision.services[0].code == "00-000152"
    assert decision.direction == "КС"
    assert decision.match_source == "info_strict"
    assert decision.confidence_level == ConfidenceLevel.HIGH
    assert not _needs_rag_fallback(decision)


def test_info_unclear_routes_to_ud_ks(engine):
    decision = route_email(
        _email(subject=".", sender_email="someone@example.com"),
        combined_text="",
        recipient="info@turbo-don.ru",
        engine=engine,
    )
    assert decision.services[0].code == "00-000066"
    assert decision.direction == "КС"
    assert decision.match_source == "info_strict_unclear"
    assert decision.confidence_level in {ConfidenceLevel.MEDIUM, ConfidenceLevel.HIGH}
    assert not _needs_rag_fallback(decision)


def test_info_strong_content_not_overridden_by_unclear(engine):
    decision = route_email(
        _email(subject="Претензия"),
        combined_text="Направляем претензию по договору, готовим иск",
        recipient="info@turbo-don.ru",
        engine=engine,
    )
    assert decision.services[0].code == "00-000044"
    assert decision.match_source == "content"


def test_tpp_routes_to_chairman_on_any_mailbox(engine):
    decision = route_email(
        _email(
            mailbox="sales@turbo-don.ru",
            routing_recipient="sales@turbo-don.ru",
            subject="Приглашение",
        ),
        combined_text="Торгово-промышленная палата Ростовской области направляет приглашение",
        recipient="sales@turbo-don.ru",
        engine=engine,
    )
    assert decision.services[0].code == "00-000001"
    assert decision.match_source == "institution_chairman"
    assert decision.confidence_level == ConfidenceLevel.HIGH
    assert not _needs_rag_fallback(decision)


def test_apgo_routes_to_chairman(engine):
    decision = route_email(
        _email(subject="Документы АПГО"),
        combined_text="АПГО направляет материалы для рассмотрения",
        recipient="info@turbo-don.ru",
        engine=engine,
    )
    assert decision.services[0].code == "00-000001"
    assert decision.match_source == "institution_chairman"


def test_other_mailbox_amural_not_forced_to_info_rules(engine):
    decision = route_email(
        _email(
            mailbox="jurist@turbo-don.ru",
            routing_recipient="jurist@turbo-don.ru",
            subject="Амураль Игорь Борисович",
        ),
        combined_text="Амураль Игорь Борисович, материалы по делу",
        recipient="jurist@turbo-don.ru",
        engine=engine,
    )
    assert decision.match_source != "info_strict"
    assert decision.services[0].code == "00-000044"


def test_ministry_routes_to_operational_director_on_any_mailbox(engine):
    decision = route_email(
        _email(
            mailbox="officemanager@turbo-don.ru",
            routing_recipient="officemanager@turbo-don.ru",
            subject="Министерство",
        ),
        combined_text="Письмо из министерства промышленности",
        recipient="officemanager@turbo-don.ru",
        engine=engine,
    )
    assert decision.services[0].code == "00-000152"
    assert decision.match_source == "institution_operational_director"
    assert decision.confidence_level == ConfidenceLevel.HIGH
    assert not _needs_rag_fallback(decision)


def test_administration_routes_to_operational_director(engine):
    decision = route_email(
        _email(
            mailbox="sales@turbo-don.ru",
            routing_recipient="sales@turbo-don.ru",
            subject="Запрос",
        ),
        combined_text="Администрация города Ростова-на-Дону направляет документы",
        recipient="sales@turbo-don.ru",
        engine=engine,
    )
    assert decision.services[0].code == "00-000152"
    assert decision.match_source == "institution_operational_director"


def test_amural_beats_ministry_on_info(engine):
    decision = route_email(
        _email(subject="Министерство / Амураль"),
        combined_text="Министерство направляет письмо на имя Амураль И.Б.",
        recipient="info@turbo-don.ru",
        engine=engine,
    )
    assert decision.services[0].code == "00-000001"
    assert decision.match_source == "info_strict"
