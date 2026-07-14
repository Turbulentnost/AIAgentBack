"""Тесты продолжения пайплайна после human-in-the-loop."""

from __future__ import annotations

from datetime import datetime, timezone

from agent_pochta.schemas import EmailMessage, ProcessingStatus, Priority, RoutingResult
from agent_pochta.services import build_container
from agent_pochta.workers.hitl import continue_after_human_approval


def _email(**kw) -> EmailMessage:
    base = dict(
        message_id="<hitl@example>",
        mailbox="test_ii@turbo-don.ru",
        sender_email="npo_ii4@turbo-don.ru",
        subject="FW: Акт сверки",
        body_text="Просим подписать акт сверки за 2 квартал.",
        received_at=datetime.now(timezone.utc),
    )
    base.update(kw)
    return EmailMessage(**base)


def test_continue_after_human_produces_summary_and_done():
    container = build_container()
    routing = RoutingResult(
        department_id="FINANCE",
        department_name="Финансы",
        confidence=0.35,
        reasoning="Низкая уверенность LLM",
        priority=Priority.NORMAL,
    )

    result = continue_after_human_approval(
        email=_email(),
        routing=routing,
        container=container,
    )

    assert result["status"] == ProcessingStatus.DONE
    assert result.get("summary_ru")
    assert "summarize" in result["trace"]
    assert result["trace"][-1] == "finalize"


def test_continue_after_human_reuses_existing_summary():
    container = build_container()
    routing = RoutingResult(
        department_id="FINANCE",
        department_name="Финансы",
        confidence=0.35,
        reasoning="Низкая уверенность LLM",
        priority=Priority.NORMAL,
    )
    existing = "Готовый обзор от первого прогона."

    result = continue_after_human_approval(
        email=_email(),
        routing=routing,
        container=container,
        summary_ru=existing,
        meta={"xml_document": "<document></document>"},
    )

    assert result["status"] == ProcessingStatus.DONE
    assert result.get("summary_ru") == existing
    assert "summarize" not in result["trace"]
    assert "process_content" not in result["trace"]
    assert "create_erp_task" in result["trace"]
    assert result["trace"][-1] == "finalize"
