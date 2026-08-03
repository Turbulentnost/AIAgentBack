"""Балльная уверенность отделов: Председатель / ОД / ВЭД."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agent_pochta.nodes.n5_route_department import (
    _has_hard_foreign_evidence,
    _reject_ved_without_hard_foreign,
)
from agent_pochta.routing import RouteEngine, route_email
from agent_pochta.routing.deterministic_sales import (
    foreign_confirm_markers_in_text,
    is_commercial_ru_context,
    reset_deterministic_sales_rules_cache,
)
from agent_pochta.routing.evidence import (
    CHAIRMAN_DEPARTMENT_CODE,
    OD_DEPARTMENT_CODE,
    VED_DEPARTMENT_CODE,
    department_confidence_accepted,
    evaluate_route_confidence,
    score_to_level,
)
from agent_pochta.routing.models import ConfidenceLevel
from agent_pochta.schemas import EmailMessage, RoutingResult


@pytest.fixture(autouse=True)
def _reset_det_cache():
    reset_deterministic_sales_rules_cache()
    yield
    reset_deterministic_sales_rules_cache()


@pytest.fixture
def engine() -> RouteEngine:
    return RouteEngine.load()


def _email(**overrides: str) -> EmailMessage:
    values = {
        "message_id": "<dept-confidence@example>",
        "mailbox": "info@turbo-don.ru",
        "routing_recipient": "info@turbo-don.ru",
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


def test_score_to_level_thresholds():
    assert score_to_level(98) == ConfidenceLevel.CRITICAL
    assert score_to_level(95) == ConfidenceLevel.HIGH
    assert score_to_level(70) == ConfidenceLevel.MEDIUM
    assert score_to_level(69) == ConfidenceLevel.LOW


def test_chairman_hard_reaches_gate():
    result = evaluate_route_confidence(
        match_source="institution_chairman",
        department_code=CHAIRMAN_DEPARTMENT_CODE,
        matched_keywords=["тпп ростов"],
        org_confirmed=True,
    )
    assert result.hard_count >= 1
    assert result.score >= 98
    assert result.level == ConfidenceLevel.CRITICAL
    ok, reason = department_confidence_accepted(
        department_code=CHAIRMAN_DEPARTMENT_CODE,
        score=result.score,
        hard_count=result.hard_count,
        adaptive_count=result.adaptive_count,
        hard_foreign=False,
    )
    assert ok, reason


def test_od_hard_reaches_gate():
    result = evaluate_route_confidence(
        match_source="institution_operational_director",
        department_code=OD_DEPARTMENT_CODE,
        matched_keywords=["министерство"],
    )
    assert result.score >= 95
    assert result.level in {ConfidenceLevel.HIGH, ConfidenceLevel.CRITICAL}
    ok, _ = department_confidence_accepted(
        department_code=OD_DEPARTMENT_CODE,
        score=result.score,
        hard_count=result.hard_count,
        adaptive_count=result.adaptive_count,
        hard_foreign=False,
    )
    assert ok


def test_ved_hard_foreign_reaches_gate():
    result = evaluate_route_confidence(
        match_source="det_foreign_domain",
        department_code=VED_DEPARTMENT_CODE,
        matched_keywords=["partner.de"],
        foreign_confirm_markers=["инкотермс"],
    )
    assert result.hard_foreign
    assert result.score >= 90
    ok, _ = department_confidence_accepted(
        department_code=VED_DEPARTMENT_CODE,
        score=result.score,
        hard_count=result.hard_count,
        adaptive_count=result.adaptive_count,
        hard_foreign=True,
    )
    assert ok


def test_ved_without_hard_foreign_rejected_by_gate():
    result = evaluate_route_confidence(
        match_source="content",
        department_code=VED_DEPARTMENT_CODE,
        content_hits=5,
        matched_keywords=["экспорт", "зарубеж", "таможен"],
        apply_floor=False,
    )
    ok, reason = department_confidence_accepted(
        department_code=VED_DEPARTMENT_CODE,
        score=max(result.score, 95),
        hard_count=0,
        adaptive_count=result.adaptive_count,
        hard_foreign=False,
    )
    assert not ok
    assert reason == "ved_requires_hard_foreign"


def test_ru_sales_does_not_route_to_ved(engine):
    decision = _route(
        engine,
        "Запрос цен на расходомер, нужна спецификация и ТКП.",
        sender_email="sales@client.yandex.ru",
        mailbox="sales@turbo-don.ru",
        routing_recipient="sales@turbo-don.ru",
    )
    assert decision.services[0].code != VED_DEPARTMENT_CODE
    assert decision.match_source != "det_foreign_domain"


def test_foreign_domain_routes_ved_with_high_confidence(engine):
    decision = _route(
        engine,
        "Inquiry Incoterms FOB. Contact sales@partner-export.de",
        sender_email="client@partner-export.de",
        mailbox="sales@turbo-don.ru",
        routing_recipient="sales@turbo-don.ru",
    )
    assert decision.services[0].code == VED_DEPARTMENT_CODE
    assert decision.match_source == "det_foreign_domain"
    assert decision.hard_foreign is True
    assert decision.confidence_score >= 90


def test_chairman_institution_live_route(engine):
    decision = _route(
        engine,
        "Письмо от торгово-промышленная палата Ростовской области.",
        sender_email="info@tppro.ru",
    )
    assert decision.services[0].code == CHAIRMAN_DEPARTMENT_CODE
    assert decision.confidence_score >= 98
    assert decision.hard_signal_count >= 1


def test_od_ministry_live_route(engine):
    decision = _route(
        engine,
        "Обращение Министерства промышленности по проектным работам узлов учета.",
        sender_email="mail@minprom.gov.ru",
    )
    assert decision.services[0].code == OD_DEPARTMENT_CODE
    assert decision.confidence_score >= 95


def test_commercial_ru_context_helper():
    assert is_commercial_ru_context(
        subject="Запрос цен",
        body="Просим выслать ТКП",
        sender_email="buyer@mail.ru",
    )
    assert not is_commercial_ru_context(
        subject="Запрос цен",
        body="Просим выслать ТКП",
        sender_email="buyer@hebei.cn",
    )


def test_foreign_confirm_markers_do_not_alone_mean_route():
    markers = foreign_confirm_markers_in_text("Нужен export и Incoterms CIF")
    assert markers
    # Без домена — не hard foreign route (проверяется в test_deterministic_sales).


def test_reject_llm_ved_without_hard_foreign():
    email = _email(sender_email="a@yandex.ru", body_text="Запрос цен на оборудование")
    decision = type(
        "D",
        (),
        {
            "hard_foreign": False,
            "match_source": "content",
            "confidence_score": 40,
            "services": [],
        },
    )()
    llm_routing = RoutingResult(
        department_id=VED_DEPARTMENT_CODE,
        department_name="ВЭД",
        confidence=0.95,
        reasoning="llm guess",
    )
    fallback = RoutingResult(
        department_id="00-000155",
        department_name="ОДП",
        confidence=0.7,
        reasoning="rules",
    )
    out, trace = _reject_ved_without_hard_foreign(
        llm_routing,
        decision=decision,
        email=email,
        text=email.body_text or "",
        fallback=fallback,
        trace=[],
    )
    assert out.department_id != VED_DEPARTMENT_CODE
    assert any("ved_blocked" in t for t in trace)
    assert not _has_hard_foreign_evidence(
        decision=decision, email=email, text=email.body_text or ""
    )


def test_chairman_adaptive_only_needs_four_clusters():
    ok, reason = department_confidence_accepted(
        department_code=CHAIRMAN_DEPARTMENT_CODE,
        score=99,
        hard_count=0,
        adaptive_count=3,
        hard_foreign=False,
    )
    assert not ok
    ok2, _ = department_confidence_accepted(
        department_code=CHAIRMAN_DEPARTMENT_CODE,
        score=99,
        hard_count=0,
        adaptive_count=4,
        hard_foreign=False,
    )
    assert ok2
