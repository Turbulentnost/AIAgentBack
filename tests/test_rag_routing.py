"""Тесты RAG fallback в узле route_department."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agent_pochta.graph import build_graph
from agent_pochta.nodes.n5_route_department import _needs_rag_fallback, _rag_department_candidates
from agent_pochta.routing import route_email
from agent_pochta.routing.models import ConfidenceLevel
from agent_pochta.routing.recipients import build_routing_search_text
from agent_pochta.schemas import EmailMessage, ProcessingStatus
from agent_pochta.services import build_container
from agent_pochta.services.routing_departments import build_departments_from_rules, load_routing_rules


def _email(**kw) -> EmailMessage:
    base = dict(
        message_id="<rag@example>",
        mailbox="info@turbo-don.ru",
        sender_email="unknown@example.ru",
        subject="Запрос",
        body_text="",
        received_at=datetime.now(timezone.utc),
        routing_recipient="misc@turbo-don.ru",
    )
    base.update(kw)
    return EmailMessage(**base)


def test_needs_rag_fallback_reserve_route():
    decision = route_email(
        _email(subject="Общий вопрос", body_text="Добрый день."),
        combined_text="Добрый день.",
        recipient="misc@turbo-don.ru",
    )
    assert decision.match_source == "reserve"
    assert _needs_rag_fallback(decision)


def test_needs_rag_fallback_skipped_for_exact_email():
    decision = route_email(
        _email(subject="Тендер"),
        combined_text="Закупочная процедура",
        recipient="tender@turbo-don.ru",
    )
    assert decision.confidence_level in {ConfidenceLevel.HIGH, ConfidenceLevel.MEDIUM}
    assert not _needs_rag_fallback(decision)


def test_rag_fallback_picks_department_from_keywords(monkeypatch):
    monkeypatch.setenv("RAG_DEPARTMENT_ENABLED", "true")
    monkeypatch.setenv("RAG_BACKEND", "stub")
    monkeypatch.setenv("USE_STUBS", "true")
    from agent_pochta.config import reset_settings

    reset_settings()
    app = build_graph()
    res = app.invoke(
        {
            "email": _email(
                message_id="<rag-sales@example>",
                subject="Счёт",
                body_text="Просьба выставить счёт на поставку оборудования.",
            )
        }
    )
    assert "route_department_rag" in res["trace"]
    assert res["routing"].department_id == "00-000155"
    assert (res.get("meta") or {}).get("rag_fallback") is True


def test_rag_respects_sender_allowed_departments(monkeypatch):
    monkeypatch.setenv("RAG_DEPARTMENT_ENABLED", "true")
    from agent_pochta.config import reset_settings
    from agent_pochta.schemas import Contractor, SenderIdentity
    from agent_pochta.services.rag import StubRAGService

    reset_settings()
    container = build_container()
    container.rag = StubRAGService()
    # keyword StubRAG: «претензия» (не «претензию» — substring match)
    body = "Направляем претензия по срокам поставки и требование от госоргана."
    state = {
        "email": _email(
            subject="Обращение",
            body_text=body,
        ),
        "combined_text": body,
        "sender": SenderIdentity(
            found=True,
            contractor=Contractor(
                contractor_id="C-GOV-01",
                name="ИФНС",
                emails=["info@nalog.gov.ru"],
                department_codes=["00-000044"],
                contractor_type="госорган",
            ),
            allowed_departments=["00-000044"],
        ),
        "trace": [],
    }
    from agent_pochta.nodes.n5_route_department import node_route_department

    res = node_route_department(state, container)
    assert res["routing"].department_id == "00-000044"
    assert "route_department_rag" in res["trace"]


def test_rag_disabled_keeps_rule_department(monkeypatch):
    monkeypatch.setenv("RAG_DEPARTMENT_ENABLED", "false")
    from agent_pochta.config import reset_settings

    reset_settings()
    app = build_graph()
    res = app.invoke(
        {
            "email": _email(
                message_id="<no-rag@example>",
                subject="Счёт",
                body_text="Просьба выставить счёт на поставку.",
            )
        }
    )
    assert "route_department_rag" not in res["trace"]
    assert res["routing"].department_id == "00-000002"


def test_build_routing_search_text_prepends_recipient():
    text = build_routing_search_text(
        recipient="jurist@turbo-don.ru",
        subject="Вопрос",
        body="Добрый день.",
    )
    assert text.startswith("jurist@turbo-don.ru jurist")
    assert "Вопрос" in text


def test_route_uses_routing_recipient_not_mailbox():
    decision = route_email(
        _email(
            mailbox="info@turbo-don.ru",
            subject="Общий вопрос",
            body_text="Добрый день.",
        ),
        combined_text="Добрый день.",
        recipient="jurist@turbo-don.ru",
    )
    assert decision.services[0].code == "00-000044"
    assert decision.match_source == "exact_email"


def test_rag_search_prefers_recipient_local_part():
    from agent_pochta.services.rag import score_department_keywords

    departments = build_departments_from_rules(load_routing_rules())
    jurist_dept = next(d for d in departments if d.department_id == "00-000044")
    opmu_dept = next(d for d in departments if d.department_id == "00-000074")

    body_only = score_department_keywords(jurist_dept, "Добрый день.", recipient=None)
    with_recipient = score_department_keywords(
        jurist_dept,
        build_routing_search_text(
            recipient="jurist@turbo-don.ru",
            body="Добрый день.",
        ),
        recipient="jurist@turbo-don.ru",
    )
    opmu_with_jurist_recipient = score_department_keywords(
        opmu_dept,
        build_routing_search_text(
            recipient="jurist@turbo-don.ru",
            body="Добрый день.",
        ),
        recipient="jurist@turbo-don.ru",
    )

    assert body_only == 0
    assert with_recipient > opmu_with_jurist_recipient


def test_score_ignores_hyphen_and_stopword_keywords():
    """Substring-matching «-» / «и» / «info» / «turbo» не должен поднимать отделы на каждом @turbo-don.ru."""
    from agent_pochta.schemas import Department
    from agent_pochta.services.rag import score_department_keywords

    noisy = Department(
        department_id="NOISE",
        department_name="Шум",
        head_name="—",
        responsibility="",
        keywords=["-", "и", "по", "info", "на", "turbo", "омто"],
    )
    clean = Department(
        department_id="CLEAN",
        department_name="Чистый",
        head_name="—",
        responsibility="",
        keywords=["акт сверки", "бухгалтерия"],
    )
    text = build_routing_search_text(
        recipient="info@turbo-don.ru",
        subject="Вопрос",
        body="Добрый день.",
    )
    assert score_department_keywords(noisy, text, recipient="info@turbo-don.ru") == 0
    assert score_department_keywords(clean, text, recipient="info@turbo-don.ru") == 0
    assert (
        score_department_keywords(
            clean,
            "Просьба прислать акт сверки.",
            recipient="info@turbo-don.ru",
        )
        >= 1
    )


def test_rag_fallback_routes_by_recipient_when_body_generic(monkeypatch):
    from agent_pochta.schemas import Department
    from agent_pochta.services.rag import score_department_keywords

    class _RulesRAG:
        def __init__(self) -> None:
            self._departments = {
                d.department_id: d for d in build_departments_from_rules(load_routing_rules())
            }

        def search_departments(self, text, top_k=3, *, recipient=None):
            scored = [
                (score_department_keywords(d, text, recipient=recipient), d)
                for d in self._departments.values()
            ]
            scored.sort(key=lambda item: item[0], reverse=True)
            ranked = [dept for score, dept in scored if score > 0]
            return ranked[:top_k]

        def find_contractor_by_email(self, email):  # noqa: ARG002
            return None

        def get_department(self, department_id: str) -> Department | None:
            return self._departments.get(department_id)

    container = build_container()
    container.rag = _RulesRAG()
    candidates = _rag_department_candidates(
        container,
        "Добрый день.",
        None,
        recipient="td_opmu1@turbo-don.ru",
        subject="Вопрос",
    )
    assert candidates[0]["department_id"] == "00-000074"
    assert all("head_name" not in c for c in candidates)
    assert all({"department_id", "department_name"} <= set(c) for c in candidates)


def test_llm_zero_dept_confidence_falls_back_to_rule_score():
    from agent_pochta.nodes.n5_route_department import _with_fallback_confidence
    from agent_pochta.schemas import RoutingResult

    llm = RoutingResult(
        department_id="00-000065",
        department_name="ОМТО",
        confidence=0.0,
        reasoning="LLM без score",
    )
    rules = RoutingResult(
        department_id="00-000066",
        department_name="Резерв",
        confidence=0.45,
        reasoning="rule",
    )
    merged = _with_fallback_confidence(
        llm,
        fallback=rules,
        decision_score=45,
        decision_level=ConfidenceLevel.LOW,
    )
    assert merged.department_id == "00-000065"
    assert merged.confidence == pytest.approx(0.45)
