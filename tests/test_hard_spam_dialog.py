"""Жёсткий спам не должен классифицироваться как «Диалог»."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from agent_pochta.graph import build_graph
from agent_pochta.nodes.n5_route_department import node_route_department
from agent_pochta.routing.models import ConfidenceLevel, RoutingDecision, ServiceRoute
from agent_pochta.schemas import EmailMessage, ProcessingStatus, RoutingResult, SpamResult
from agent_pochta.services import build_container
from agent_pochta.services.llm_analyze import IncomingEmailAnalysis


def _email(**kw) -> EmailMessage:
    base = dict(
        message_id="<hard-spam-dialog@test>",
        mailbox="info@turbo-don.ru",
        sender_email="partner@example.ru",
        subject="Re: сроки поставки",
        body_text="",
        received_at=datetime.now(timezone.utc),
        routing_recipient="info@turbo-don.ru",
    )
    base.update(kw)
    return EmailMessage(**base)


def _dialog_body(*, spam_line: str) -> str:
    return (
        "Спасибо, принято.\n\n"
        "С уважением, ООО НПО «Турбулентность-ДОН»\n"
        "10.07.2026, manager@turbo-don.ru пишет:\n"
        "> Уточните срок\n"
        "ООО НПО «Турбулентность-ДОН» — ответ\n"
        f"{spam_line}"
    )


def test_graph_hard_spam_with_dialog_markers_stays_spam():
    app = build_graph()
    res = app.invoke(
        {
            "email": _email(
                subject="Re: сроки поставки",
                body_text=_dialog_body(spam_line="Приглашаем на бесплатный вебинар"),
            )
        }
    )
    assert res["status"] == ProcessingStatus.SPAM
    assert res["status"] != ProcessingStatus.DIALOG
    assert "route_department" not in res["trace"]


def test_graph_fwd_turbulentnost_stop_word_is_spam_not_dialog():
    app = build_graph()
    res = app.invoke(
        {
            "email": _email(
                subject="Fwd: переписка по заказу",
                body_text=_dialog_body(spam_line="Только сегодня выгодное предложение"),
            )
        }
    )
    assert res["status"] == ProcessingStatus.SPAM
    assert res.get("meta", {}).get("dialog") is None


def test_route_department_hard_spam_blocks_dialog(monkeypatch: pytest.MonkeyPatch):
    email = _email(
        subject="Re: сроки поставки",
        body_text=_dialog_body(spam_line="Только сегодня выгодное предложение"),
    )
    analysis = IncomingEmailAnalysis(
        spam=SpamResult(is_spam=False, confidence=0.05, reason="LLM: не спам"),
        routing=RoutingResult(
            department_id="00-000066",
            department_name="Управление делами",
            confidence=0.8,
            reasoning="rule",
        ),
        summary_ru="Краткий обзор",
        xml_theme="Ознакомление: переписка",
        partner_name='ООО "Пример"',
        process_type="ознакомление",
    )
    llm = MagicMock()
    llm.analyze_incoming.return_value = analysis
    container = build_container()
    container.llm = llm

    decision = RoutingDecision(
        organization="НП",
        direction="КС",
        process="ознакомление",
        services=[ServiceRoute(code="00-000066", name="Управление делами", reasoning="rule")],
        confidence_level=ConfidenceLevel.HIGH,
        confidence_score=90,
        match_source="exact_email",
        xml_document="<document/>",
    )

    monkeypatch.setenv("LLM_GATEWAY_URL", "http://llm")
    monkeypatch.setenv("USE_STUBS", "false")
    from agent_pochta.config import reset_settings

    reset_settings()

    with patch(
        "agent_pochta.nodes.n5_route_department.route_email",
        return_value=decision,
    ):
        with patch(
            "agent_pochta.nodes.n5_route_department.rebuild_decision_xml",
            side_effect=lambda email_obj, dec, **kwargs: dec,
        ):
            result = node_route_department(
                {
                    "email": email,
                    "combined_text": email.body_text or "",
                    "attachments_text": "",
                    "trace": [],
                },
                container=container,
            )

    assert result["status"] == ProcessingStatus.SPAM
    assert "hard_spam_skip_dialog" in result["trace"]
    assert "dialog" not in (result.get("meta") or {})


def test_route_department_restored_from_spam_allows_dialog_review(monkeypatch: pytest.MonkeyPatch):
    email = _email(
        subject="Re: сроки поставки",
        body_text=_dialog_body(spam_line="Приглашаем на бесплатный вебинар"),
    )
    analysis = IncomingEmailAnalysis(
        spam=SpamResult(is_spam=False, confidence=0.05, reason="skipped"),
        routing=RoutingResult(
            department_id="00-000066",
            department_name="Управление делами",
            confidence=0.8,
            reasoning="rule",
        ),
        summary_ru="Краткий обзор",
        xml_theme="Диалог: переписка",
        partner_name='ООО "Пример"',
        process_type="ознакомление",
    )
    llm = MagicMock()
    llm.analyze_incoming.return_value = analysis
    container = build_container()
    container.llm = llm

    decision = RoutingDecision(
        organization="НП",
        direction="КС",
        process="ознакомление",
        services=[ServiceRoute(code="00-000066", name="Управление делами", reasoning="rule")],
        confidence_level=ConfidenceLevel.HIGH,
        confidence_score=90,
        match_source="exact_email",
        xml_document="<document/>",
    )

    monkeypatch.setenv("LLM_GATEWAY_URL", "http://llm")
    monkeypatch.setenv("USE_STUBS", "false")
    from agent_pochta.config import reset_settings

    reset_settings()

    with patch(
        "agent_pochta.nodes.n5_route_department.route_email",
        return_value=decision,
    ):
        with patch(
            "agent_pochta.nodes.n5_route_department.rebuild_decision_xml",
            side_effect=lambda email_obj, dec, **kwargs: dec,
        ):
            result = node_route_department(
                {
                    "email": email,
                    "combined_text": email.body_text or "",
                    "attachments_text": "",
                    "meta": {"restored_from_spam": True},
                    "trace": [],
                },
                container=container,
            )

    assert result["status"] == ProcessingStatus.AWAITING_HUMAN
    assert "hard_spam_skip_dialog" not in result["trace"]
    assert (result.get("meta") or {}).get("dialog", {}).get("mode") == "dormant"
