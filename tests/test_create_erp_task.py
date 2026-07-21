"""Тесты узла создания задачи в 1С."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

from agent_pochta.nodes.n7_create_erp_task import node_create_erp_task
from agent_pochta.schemas import EmailMessage, ProcessingStatus, Priority, RoutingResult


def _state(*, trace: list[str] | None = None) -> dict:
    return {
        "email": EmailMessage(
            message_id="<erp@example>",
            mailbox="info@turbo-don.ru",
            sender_email="vendor@example.com",
            subject="Акт",
            body_text="Текст",
            received_at=datetime.now(timezone.utc),
        ),
        "routing": RoutingResult(
            department_id="FINANCE",
            department_name="Финансы",
            confidence=1.0,
            reasoning="Подтверждено оператором",
            priority=Priority.NORMAL,
        ),
        "summary_ru": "Обзор.",
        "trace": trace or ["human_approved"],
        "meta": {"xml_document": "<document></document>"},
    }


def test_create_erp_task_human_approved_failure_does_not_set_error(monkeypatch):
    container = MagicMock()
    container.integration.create_incoming_correspondence.side_effect = RuntimeError("1C timeout")

    monkeypatch.setattr("agent_pochta.nodes.n7_create_erp_task.get_settings", lambda: MagicMock(agent_mode="live"))

    result = node_create_erp_task(_state(), container)

    assert result["erp"].success is False
    assert "status" not in result
    assert result.get("human_review") is False
    assert result["meta"]["erp_retry_scheduled"] is True


def test_create_erp_task_unapproved_failure_sets_error(monkeypatch):
    container = MagicMock()
    container.integration.create_incoming_correspondence.side_effect = RuntimeError("1C timeout")

    monkeypatch.setattr("agent_pochta.nodes.n7_create_erp_task.get_settings", lambda: MagicMock(agent_mode="live"))

    result = node_create_erp_task(_state(trace=["route_department"]), container)

    assert result["status"] == ProcessingStatus.ERROR
    assert result.get("human_review") is True


def test_create_erp_task_skips_non_info_mailbox(monkeypatch):
    container = MagicMock()
    monkeypatch.setattr("agent_pochta.nodes.n7_create_erp_task.get_settings", lambda: MagicMock(agent_mode="live"))

    state = _state()
    state["email"] = state["email"].model_copy(
        update={
            "mailbox": "td_sales2.8@turbo-don.ru",
            "routing_recipient": "td_sales2.8@turbo-don.ru",
        }
    )

    result = node_create_erp_task(state, container)

    assert result["erp"].success is True
    assert result["erp"].erp_document_number == "SKIP-ERP"
    assert result["meta"]["erp_skipped"] is True
    assert "info@turbo-don.ru" in result["meta"]["erp_skip_reason"]
    container.integration.create_incoming_correspondence.assert_not_called()
