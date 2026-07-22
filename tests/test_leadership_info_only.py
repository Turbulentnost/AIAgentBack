"""Руководство (председатель/ОД/помощник) — только для info@turbo-don.ru."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agent_pochta.routing import RouteEngine, route_email
from agent_pochta.routing.organizations import (
    INFO_LEADERSHIP_MAILBOX,
    leadership_department_allowed,
)
from agent_pochta.schemas import EmailMessage
from agent_pochta.services.odata_incoming_mapper import (
    build_incoming_document_payload,
    validate_leadership_routing_for_erp,
)


def _email(**kw) -> EmailMessage:
    base = dict(
        message_id="<leadership-info-only@example>",
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
    ("department_id", "match_source", "recipient", "allowed"),
    [
        ("00-000001", "institution_chairman", INFO_LEADERSHIP_MAILBOX, True),
        ("00-000152", "institution_operational_director", INFO_LEADERSHIP_MAILBOX, True),
        ("00-000182", "ud_transfer", INFO_LEADERSHIP_MAILBOX, True),
        ("00-000152", "institution_operational_director", "sales@turbo-don.ru", False),
        ("00-000001", "det_chairman", "sales@turbo-don.ru", False),
        ("00-000152", "exact_email", "npo_uprdir@turbo-don.ru", True),
        ("00-000152", "email_keyword", "uprdir@turbo-don.ru", True),
        ("00-000152", "content", "sales@turbo-don.ru", False),
        ("00-000152", "human_correction", "sales@turbo-don.ru", True),
    ],
)
def test_leadership_department_allowed_matrix(
    department_id: str,
    match_source: str,
    recipient: str,
    allowed: bool,
) -> None:
    assert (
        leadership_department_allowed(
            recipient=recipient,
            department_code=department_id,
            match_source=match_source,
        )
        is allowed
    )


def test_dedicated_uprdir_mailbox_still_routes_to_operational_director(engine) -> None:
    decision = route_email(
        _email(
            mailbox="npo_uprdir@turbo-don.ru",
            routing_recipient="npo_uprdir@turbo-don.ru",
            subject="Документы",
        ),
        combined_text="Материалы для рассмотрения",
        recipient="npo_uprdir@turbo-don.ru",
        engine=engine,
    )
    assert decision.services[0].code == "00-000152"
    assert decision.match_source == "exact_email"


def test_content_ministry_on_info_routes_to_operational_director(engine) -> None:
    decision = route_email(
        _email(subject="Запрос"),
        combined_text="Министерство промышленности направляет документы",
        recipient=INFO_LEADERSHIP_MAILBOX,
        engine=engine,
    )
    assert decision.services[0].code == "00-000152"
    assert decision.match_source in {"institution_operational_director", "info_strict"}


def test_content_ministry_off_info_not_routed_to_operational_director(engine) -> None:
    decision = route_email(
        _email(
            mailbox="sales@turbo-don.ru",
            routing_recipient="sales@turbo-don.ru",
            subject="Запрос",
        ),
        combined_text="Министерство промышленности направляет документы",
        recipient="sales@turbo-don.ru",
        engine=engine,
    )
    assert decision.services[0].code != "00-000152"


def test_validate_leadership_routing_for_erp_rejects_off_info() -> None:
    with pytest.raises(ValueError, match="00-000152"):
        validate_leadership_routing_for_erp(
            department_id="00-000152",
            email_recipient="sales@turbo-don.ru",
            match_source="institution_operational_director",
        )


def test_validate_leadership_routing_for_erp_allows_info() -> None:
    validate_leadership_routing_for_erp(
        department_id="00-000001",
        email_recipient=INFO_LEADERSHIP_MAILBOX,
        match_source="info_strict",
    )


def test_odata_payload_rejects_leadership_off_info() -> None:
    from agent_pochta.schemas import RoutingResult

    email = _email(
        mailbox="sales@turbo-don.ru",
        routing_recipient="sales@turbo-don.ru",
    )
    routing = RoutingResult(
        department_id="00-000152",
        department_name="ОПЕРАЦИОННЫЙ ДИРЕКТОР",
        confidence=0.9,
        reasoning="test",
    )
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<document>"
        "<email_recipient>sales@turbo-don.ru</email_recipient>"
        "<organization>НП</organization>"
        "<direction>КС</direction>"
        "<department>00-000152</department>"
        "</document>"
    )
    with pytest.raises(ValueError, match="00-000152"):
        build_incoming_document_payload(
            email,
            routing,
            summary_ru="тест",
            xml_document=xml,
        )
