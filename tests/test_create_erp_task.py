"""Тесты узла создания задачи в 1С."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

from agent_pochta.nodes.n7_create_erp_task import node_create_erp_task
from agent_pochta.schemas import Attachment, EmailMessage, ProcessingStatus, Priority, RoutingResult


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


def test_create_erp_task_attach_failure_schedules_retry(monkeypatch):
    container = MagicMock()
    container.integration.create_incoming_correspondence.return_value = {
        "erp_document_number": "ВК-000001",
        "erp_document_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        "erp_task_id": None,
    }
    container.integration.attach_files_to_incoming_correspondence.side_effect = RuntimeError(
        "Пустое хранилище файла после POST"
    )

    monkeypatch.setattr("agent_pochta.nodes.n7_create_erp_task.get_settings", lambda: MagicMock(agent_mode="live"))

    result = node_create_erp_task(_state(), container)

    assert result["erp"].success is False
    assert result["erp"].erp_document_number == "ВК-000001"
    assert result["meta"]["erp_retry_scheduled"] is True
    assert result["meta"]["erp_attachment_errors"]


def test_create_erp_task_attaches_files_after_document_create(monkeypatch):
    container = MagicMock()
    container.integration.create_incoming_correspondence.return_value = {
        "erp_document_number": "ВК-000001",
        "erp_document_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        "erp_task_id": None,
    }
    container.integration.attach_files_to_incoming_correspondence.return_value = [
        {"ref_key": "file-ref", "filename": "scan", "size_bytes": 4}
    ]

    monkeypatch.setattr("agent_pochta.nodes.n7_create_erp_task.get_settings", lambda: MagicMock(agent_mode="live"))

    state = _state()
    state["email"] = state["email"].model_copy(
        update={
            "attachments": [
                Attachment(
                    filename="scan.pdf",
                    mime_type="application/pdf",
                    size_bytes=4,
                    content=b"1234",
                )
            ]
        }
    )

    result = node_create_erp_task(state, container)

    assert result["erp"].success is True
    container.integration.attach_files_to_incoming_correspondence.assert_called_once()
    assert result["meta"]["erp_attachments"][0]["ref_key"] == "file-ref"
