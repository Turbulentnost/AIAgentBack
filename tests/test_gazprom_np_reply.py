"""Ответ в переписке по Газпрому: НП в теле → ОПГ, без НП → Операционный директор."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agent_pochta.routing import RouteEngine, route_email
from agent_pochta.routing.models import ConfidenceLevel
from agent_pochta.routing.reply_routing import (
    has_gazprom_mention,
    has_np_marker_in_body,
    is_email_reply,
    is_reply_in_thread,
    match_gazprom_np_reply,
)
from agent_pochta.routing.xml_builder import validate_xml_document
from agent_pochta.schemas import EmailMessage

_RULES = {
    "marker": "НП",
    "enabled": True,
    "gazprom_content_patterns": ["пао газпром", "газпром"],
    "exclude_content_patterns": [
        "амураль",
        "игорь борисович",
        "председатель совета директоров",
        "председателю совета директоров",
    ],
}


def _email(**kw) -> EmailMessage:
    base = dict(
        message_id="<gazprom-np-reply@test>",
        mailbox="info@turbo-don.ru",
        sender_email="client@gazprom.ru",
        subject="",
        body_text="",
        received_at=datetime.now(timezone.utc),
    )
    base.update(kw)
    return EmailMessage(**base)


@pytest.fixture
def engine():
    return RouteEngine.load()


def test_is_email_reply_subject_prefix():
    assert is_email_reply(subject="Re: документы", body="") is True
    assert is_reply_in_thread(subject="Ответ: сроки", body="") is True


def test_is_email_reply_quoted_body():
    body = "Принято.\n\n> Уточните срок\n> Ждём ответ"
    assert is_email_reply(subject="Документы", body=body) is True


def test_has_gazprom_mention_in_body_or_sender():
    assert has_gazprom_mention(subject="", body="ПАО Газпром направляет документы") is True
    assert has_gazprom_mention(
        subject="Re: запрос",
        body="Просим уточнить срок.",
        sender_email="manager@yamburg.gazprom.ru",
    ) is True
    assert has_gazprom_mention(subject="Re: запрос", body="Просим уточнить срок.") is False


def test_has_np_marker_in_body_not_subject_only():
    assert has_np_marker_in_body("НП: направляем комплект документов", "НП") is True
    assert has_np_marker_in_body("", "НП") is False

    body = (
        "Спасибо.\n\n"
        "10.07.2026, manager@turbo-don.ru пишет:\n"
        "> Тема: Re: НП: запрос на поставку\n"
        "> Добрый день"
    )
    assert has_np_marker_in_body(body, "НП") is True


def test_np_marker_does_not_match_npo():
    assert has_np_marker_in_body("Re: НПО турбулентность-дон", "НП") is False


def test_match_gazprom_np_reply_requires_reply_gazprom_and_body_np():
    branch, hits = match_gazprom_np_reply(
        subject="Re: согласование",
        body="По теме НП направляем документы для ПАО Газпром.",
        rules=_RULES,
    )
    assert branch == "opg"
    assert hits

    branch, _ = match_gazprom_np_reply(
        subject="Re: НП: запрос",
        body="Просим уточнить срок для ПАО Газпром.",
        rules=_RULES,
    )
    assert branch == "operational_director"

    branch, _ = match_gazprom_np_reply(
        subject="НП: новый запрос",
        body="По Газпрому без ответа.",
        rules=_RULES,
    )
    assert branch is None


def test_match_gazprom_np_reply_operational_director_without_np_in_body():
    branch, hits = match_gazprom_np_reply(
        subject="Re: согласование документов",
        body="Направляем подписанный комплект по договору с ПАО Газпром.",
        sender_email="client@gazprom.ru",
        rules=_RULES,
    )
    assert branch == "operational_director"
    assert "gazprom" in hits


def test_gazprom_np_reply_routes_to_opg(engine):
    decision = route_email(
        _email(
            subject="Re: согласование документов",
            body_text="По теме НП направляем подписанный комплект для ПАО Газпром.",
        ),
        combined_text="По теме НП направляем подписанный комплект для ПАО Газпром.",
        recipient="info@turbo-don.ru",
        engine=engine,
    )
    assert decision.services[0].code == "00-000076"
    assert decision.services[0].name == "Отдел по работе с ПАО Газпром"
    assert decision.match_source == "gazprom_np_reply"
    assert decision.confidence_level in {ConfidenceLevel.HIGH, ConfidenceLevel.MEDIUM}
    assert validate_xml_document(decision.xml_document)


def test_gazprom_np_reply_from_quoted_thread(engine):
    body = (
        "Добрый день.\n\n"
        "09.07.2026, opg@turbo-don.ru пишет:\n"
        "> Тема: НП: запрос расходомера\n"
        "> Просим направить ТКП по договору с ПАО Газпром"
    )
    decision = route_email(
        _email(subject="Re: уточнение", body_text=body),
        combined_text=body,
        recipient="sales@turbo-don.ru",
        engine=engine,
    )
    assert decision.services[0].code == "00-000076"
    assert decision.match_source == "gazprom_np_reply"


def test_re_without_np_marker_routes_to_operational_director(engine):
    decision = route_email(
        _email(
            subject="Re: запрос на поставку",
            body_text="Просим уточнить срок отгрузки по договору с ПАО Газпром.",
        ),
        combined_text="Просим уточнить срок отгрузки по договору с ПАО Газпром.",
        recipient="info@turbo-don.ru",
        engine=engine,
    )
    assert decision.services[0].code == "00-000152"
    assert decision.services[0].name == "ОПЕРАЦИОННЫЙ ДИРЕКТОР"
    assert decision.match_source == "gazprom_np_reply"


def test_subject_only_np_does_not_route_to_opg(engine):
    decision = route_email(
        _email(
            subject="Re: НП: согласование документов",
            body_text="Направляем подписанный комплект для ПАО Газпром.",
        ),
        combined_text="Направляем подписанный комплект для ПАО Газпром.",
        recipient="info@turbo-don.ru",
        engine=engine,
    )
    assert decision.services[0].code == "00-000152"
    assert decision.match_source == "gazprom_np_reply"


def test_gazprom_np_reply_beats_info_strict_for_gazprom_sender(engine):
    decision = route_email(
        _email(
            sender_email="notify@office.gazprom.ru",
            subject="Re: согласование документов",
            body_text="По теме НП направляем подписанный комплект.",
        ),
        combined_text="По теме НП направляем подписанный комплект.",
        recipient="info@turbo-don.ru",
        engine=engine,
    )
    assert decision.services[0].code == "00-000076"
    assert decision.match_source == "gazprom_np_reply"


def test_chairman_marker_skips_gazprom_np_reply(engine):
    decision = route_email(
        _email(
            sender_email="notify@office.gazprom.ru",
            subject="Re: НП: материалы",
            body_text="Для Игорь Борисович направляем документы по теме НП.",
        ),
        combined_text="Для Игорь Борисович направляем документы по теме НП.",
        recipient="info@turbo-don.ru",
        engine=engine,
    )
    assert decision.match_source == "info_strict"
    assert decision.services[0].code == "00-000001"
