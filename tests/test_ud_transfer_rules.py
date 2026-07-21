"""Маршрутизация Управление делами → помощник зам. операционного директора."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agent_pochta.routing import RouteEngine, route_email
from agent_pochta.routing.models import ConfidenceLevel
from agent_pochta.routing.xml_builder import validate_xml_document
from agent_pochta.schemas import EmailMessage
from agent_pochta.services.routing_departments import (
    build_departments_from_structure,
    load_ui_department_allowlist,
)


def _email(**kw) -> EmailMessage:
    base = dict(
        message_id="<ud-transfer@test>",
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


def test_ud_transfer_routes_to_deputy_od_assistant(engine):
    decision = route_email(
        _email(
            subject="На имя заместителя операционного директора",
            body_text="Просим рассмотреть входящее письмо.",
        ),
        combined_text="Просим рассмотреть входящее письмо.",
        recipient="info@turbo-don.ru",
        engine=engine,
    )
    assert decision.services[0].code == "00-000182"
    assert decision.services[0].name == "Помощник зам. операционного директора"
    assert decision.match_source == "ud_transfer"
    assert decision.confidence_level in {ConfidenceLevel.HIGH, ConfidenceLevel.MEDIUM}
    assert validate_xml_document(decision.xml_document)


def test_ud_transfer_helper_name_pattern(engine):
    decision = route_email(
        _email(
            subject="Документы",
            body_text="Для помощника зам. операционного директора.",
        ),
        combined_text="Для помощника зам. операционного директора.",
        recipient="officemanager@turbo-don.ru",
        engine=engine,
    )
    assert decision.services[0].code == "00-000182"
    assert decision.match_source == "ud_transfer"


def test_deputy_od_assistant_in_ui_allowlist():
    allowlist = load_ui_department_allowlist()
    assert allowlist["00-000182"] == "Помощник зам. операционного директора"


def test_deputy_od_assistant_in_rag_departments():
    departments = build_departments_from_structure()
    by_id = {d.department_id: d for d in departments}
    assert "00-000182" in by_id
    assert by_id["00-000182"].department_name == "Помощник зам. операционного директора"
    keywords = " ".join(by_id["00-000182"].keywords)
    assert "помощник" in keywords
