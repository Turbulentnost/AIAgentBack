"""Политика авто-определения organization (НП по умолчанию)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agent_pochta.routing import RouteEngine, route_email
from agent_pochta.schemas import EmailMessage


def _email(**kw) -> EmailMessage:
    base = dict(
        message_id="<org@example>",
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


def test_default_organization_is_np(engine):
    assert engine.detect_organization("Добрый день, просим уточнить статус") == "НП"
    decision = route_email(
        _email(subject="Вопрос"),
        combined_text="Добрый день, общий запрос без маркеров.",
        recipient="info@turbo-don.ru",
        engine=engine,
    )
    assert decision.organization == "НП"


def test_almaz_and_grand_set_al(engine):
    assert engine.detect_organization("Письмо в ООО Алмаз") == "АЛ"
    assert engine.detect_organization("Дубликат паспорта счётчика Гранд") == "АЛ"
    assert engine.detect_organization("Запрос по Grand SPI") == "АЛ"
    decision = route_email(
        _email(subject="Гранды"),
        combined_text="Поставка счётчиков Гранд",
        recipient="info@turbo-don.ru",
        engine=engine,
    )
    assert decision.organization == "АЛ"
    assert decision.direction == "ПР"


def test_metrogazservis_sets_mg_only(engine):
    assert engine.detect_organization(
        "ООО Метрогазсервис направляет акт сверки"
    ) == "МГ"
    assert engine.detect_organization(
        "Contact from Metrogazservis warehouse"
    ) == "МГ"
    # Слабые/общие маркеры больше не поднимают МГ.
    assert engine.detect_organization("Нужен дистрибьютор и Элстер для газового учёта") == "НП"
    assert engine.detect_organization("СГБ СГК метрологический контроль") == "НП"
    assert engine.detect_organization("Вопрос по метрологии газа") != "МГ"


def test_bmi_sets_bm_only(engine):
    assert engine.detect_organization("Запрос по продукции БМИ") == "БМ"
    assert engine.detect_direction("БМ", candidate_direction="КС") == "БМ"
    decision = route_email(
        _email(subject="БМИ"),
        combined_text="Нужна консультация по БМИ без коммерческого ТКП",
        recipient="info@turbo-don.ru",
        engine=engine,
    )
    assert decision.organization == "БМ"
    assert decision.direction == "БМ"
    # ПУРГ без слова БМИ не должен автоставить организацию БМ.
    assert engine.detect_organization("Запрос на измерительный шкаф ПУРГ") == "НП"


def test_am_mi_weak_keywords_do_not_auto_set(engine):
    assert engine.detect_organization("Доставка воды и кулер для офиса") == "НП"
    assert engine.detect_organization("Гостиница, бронирование, размещение") == "НП"
    # Явное имя бренда тоже не в organization_keywords — только HITL / exact rules.
    assert engine.detect_organization("Письмо от Амурская легенда") == "НП"
    assert engine.detect_organization("Документы по объекту Милака") == "НП"


def test_recipient_mailbox_still_sets_al_mg(engine):
    assert engine.detect_organization("акт", recipient="almaz_glavbuh@turbo-don.ru") == "АЛ"
    assert engine.detect_organization("акт", recipient="mgs_buh@turbo-don.ru") == "МГ"
