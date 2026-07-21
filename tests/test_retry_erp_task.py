"""Тесты Celery retry_erp и POST /api/v1/email-messages/{id}/retry-erp."""

from __future__ import annotations

import json
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from agent_pochta.api.app import app
from agent_pochta.db.models import EmailMessageRow
from agent_pochta.schemas import EmailMessage, ProcessingStatus, Priority, RoutingResult
from agent_pochta.services.odata_incoming_mapper import resolve_payer_direction
from agent_pochta.workers.tasks import extract_xml_document_from_row, retry_erp_task

# Реальный XML из error-строки БД (lunda.ru → td_sales2.8, org=НП, направление=КС).
ERROR_CASE_XML = (
    "<document>"
    "<organization>НП</organization>"
    "<theme>Запрос: Счёт для ООО «Лунда» 00000320781</theme>"
    "<направление>КС</направление>"
    "<claim>false</claim>"
    "<partner>ООО «Лунда»</partner>"
    "<services>"
    "<service>"
    "<name>00-000155</name>"
    "<title>Отдел тендерных продаж</title>"
    "<process>исполнение</process>"
    "<reasoning>Запрос: Счёт для ООО «Лунда» 00000320781</reasoning>"
    "</service>"
    "</services>"
    "<email_sender>niani@lunda.ru</email_sender>"
    "<email_recipient>td_sales2.8@turbo-don.ru</email_recipient>"
    "<mail_datetime>2026-07-21 09:52:34</mail_datetime>"
    "<process>исполнение</process>"
    "</document>"
)

ERROR_CASE_MESSAGE_ID = (
    "<1523922645.1689750.1784616754565.JavaMail.zimbra@lunda.ru>#td_sales2.8@turbo-don.ru"
)


def _info_error_row(*, erp_retry_count: int = 1, with_xml: bool = True) -> EmailMessageRow:
    received_at = datetime(2026, 7, 21, 9, 52, 34, tzinfo=timezone.utc).replace(tzinfo=None)
    info_xml = ERROR_CASE_XML.replace(
        "td_sales2.8@turbo-don.ru",
        "info@turbo-don.ru",
    )
    message_id = "<1523922645.1689750.1784616754565.JavaMail.zimbra@lunda.ru>#info@turbo-don.ru"
    payload: dict = {
        "message_id": message_id,
        "mailbox": "info@turbo-don.ru",
        "sender_email": "niani@lunda.ru",
        "subject": 'Запрос: Счёт для ООО "Лунда" 00000320781',
        "body_text": "Просим выставить счёт.",
        "received_at": received_at.isoformat(),
        "routing_recipient": "info@turbo-don.ru",
        "to": ["info@turbo-don.ru"],
        "cc": [],
    }
    if with_xml:
        payload["xml_document"] = info_xml
    return EmailMessageRow(
        id=uuid.uuid4(),
        message_id=message_id,
        received_at=received_at,
        mailbox="info@turbo-don.ru",
        sender_email="niani@lunda.ru",
        sender_name="Лунда",
        subject='Запрос: Счёт для ООО "Лунда" 00000320781',
        status=ProcessingStatus.ERROR.value,
        department_id="00-000155",
        department_name="Отдел тендерных продаж",
        summary_ru="ИИ просит выставить счёт для ООО «Лунда».",
        erp_retry_count=erp_retry_count,
        human_review=True,
        raw_payload_json=json.dumps(payload, ensure_ascii=False),
    )


def _info_sample_email() -> EmailMessage:
    return EmailMessage(
        message_id="<1523922645.1689750.1784616754565.JavaMail.zimbra@lunda.ru>#info@turbo-don.ru",
        mailbox="info@turbo-don.ru",
        sender_email="niani@lunda.ru",
        subject='Запрос: Счёт для ООО "Лунда" 00000320781',
        received_at=datetime(2026, 7, 21, 9, 52, 34, tzinfo=timezone.utc),
        to=["info@turbo-don.ru"],
        routing_recipient="info@turbo-don.ru",
    )


def _error_row(*, erp_retry_count: int = 1, with_xml: bool = True) -> EmailMessageRow:
    received_at = datetime(2026, 7, 21, 9, 52, 34, tzinfo=timezone.utc).replace(tzinfo=None)
    payload: dict = {
        "message_id": ERROR_CASE_MESSAGE_ID,
        "mailbox": "td_sales2.8@turbo-don.ru",
        "sender_email": "niani@lunda.ru",
        "subject": 'Запрос: Счёт для ООО "Лунда" 00000320781',
        "body_text": "Просим выставить счёт.",
        "received_at": received_at.isoformat(),
        "routing_recipient": "td_sales2.8@turbo-don.ru",
    }
    if with_xml:
        payload["xml_document"] = ERROR_CASE_XML
    return EmailMessageRow(
        id=uuid.uuid4(),
        message_id=ERROR_CASE_MESSAGE_ID,
        received_at=received_at,
        mailbox="td_sales2.8@turbo-don.ru",
        sender_email="niani@lunda.ru",
        sender_name="Лунда",
        subject='Запрос: Счёт для ООО "Лунда" 00000320781',
        status=ProcessingStatus.ERROR.value,
        department_id="00-000155",
        department_name="Отдел тендерных продаж",
        summary_ru="ИИ просит выставить счёт для ООО «Лунда».",
        erp_retry_count=erp_retry_count,
        human_review=True,
        raw_payload_json=json.dumps(payload, ensure_ascii=False),
    )


def _sample_email() -> EmailMessage:
    return EmailMessage(
        message_id=ERROR_CASE_MESSAGE_ID,
        mailbox="td_sales2.8@turbo-don.ru",
        sender_email="niani@lunda.ru",
        subject='Запрос: Счёт для ООО "Лунда" 00000320781',
        received_at=datetime(2026, 7, 21, 9, 52, 34, tzinfo=timezone.utc),
    )


def _sample_routing() -> RoutingResult:
    return RoutingResult(
        department_id="00-000155",
        department_name="Отдел тендерных продаж",
        confidence=1.0,
        reasoning="Подтверждено",
        priority=Priority.NORMAL,
    )


@contextmanager
def _mock_retry_deps(
    *,
    row: EmailMessageRow,
    integration: MagicMock | None = None,
    email: EmailMessage | None = None,
):
    session = MagicMock()
    repo = MagicMock()
    repo.get_by_message_id.return_value = row
    repo.load_email_from_row.return_value = email or _sample_email()
    repo.build_routing_from_row.return_value = _sample_routing()
    repo.increment_erp_retry.return_value = row.erp_retry_count + 1

    session_factory = MagicMock()
    session_factory.return_value.__enter__.return_value = session

    container = MagicMock()
    container.integration = integration or MagicMock()

    with patch("agent_pochta.workers.tasks.get_session_factory", lambda: session_factory), patch(
        "agent_pochta.workers.tasks.EmailRepository",
        lambda _s: repo,
    ), patch(
        "agent_pochta.workers.runtime.get_worker_container",
        lambda: container,
    ), patch(
        "agent_pochta.workers.tasks.get_settings",
        lambda: MagicMock(erp_retry_max=5, erp_retry_delay_sec=600),
    ):
        yield repo, container


def test_extract_xml_document_from_row() -> None:
    row = _error_row()
    assert extract_xml_document_from_row(row) == ERROR_CASE_XML


def test_retry_erp_task_posts_minimal_payload_from_stored_xml(monkeypatch: pytest.MonkeyPatch) -> None:
    row = _info_error_row()
    info_xml = json.loads(row.raw_payload_json)["xml_document"]
    integration = MagicMock()
    integration.create_incoming_correspondence.return_value = {
        "erp_document_number": "ВК-000099",
        "erp_document_id": "11111111-2222-3333-4444-555555555555",
        "erp_task_id": None,
        "fields": {},
    }

    with _mock_retry_deps(row=row, integration=integration, email=_info_sample_email()):
        task = retry_erp_task
        task.push_request(retries=0, max_retries=5)
        try:
            result = task(row.message_id)
        finally:
            task.pop_request()

    assert result["ok"] is True
    assert result["erp_document_number"] == "ВК-000099"
    integration.create_incoming_correspondence.assert_called_once()
    kwargs = integration.create_incoming_correspondence.call_args.kwargs
    assert kwargs["xml_document"] == info_xml
    fields = integration.create_incoming_correspondence.return_value.get("fields", {})
    # Полный payload проверяется в test_odata_integration; здесь — что xml передан.
    assert kwargs["xml_document"] is not None


def test_retry_erp_task_skips_non_info_mailbox() -> None:
    row = _error_row()
    integration = MagicMock()

    with _mock_retry_deps(row=row, integration=integration):
        task = retry_erp_task
        task.push_request(retries=0, max_retries=5)
        try:
            result = task(row.message_id)
        finally:
            task.pop_request()

    assert result == {"ok": True, "skipped": True, "reason": "not_info_mailbox"}
    integration.create_incoming_correspondence.assert_not_called()


def test_retry_erp_task_minimal_payload_author_and_payer(monkeypatch: pytest.MonkeyPatch) -> None:
    """Повтор в 1С через ODataIntegrationService шлёт только Автор + ПлательщикНаправление."""
    from agent_pochta.services.odata_integration import ODataIntegrationService

    row = _info_error_row()
    service = ODataIntegrationService(
        "http://1c.local/odata/standard.odata",
        entity="Document_ТД_ВходящаяКорреспонденция",
    )

    with patch.object(
        service._client,
        "create_entity",
        return_value={"Ref_Key": "abc", "Number": "ВК-0001"},
    ) as create_mock, _mock_retry_deps(row=row, integration=service, email=_info_sample_email()):
        task = retry_erp_task
        task.push_request(retries=0, max_retries=5)
        try:
            result = task(row.message_id)
        finally:
            task.pop_request()

    assert result["ok"] is True
    payload = create_mock.call_args[0][1]
    assert set(payload.keys()) == {
        "Автор",
        "Автор_Type",
        "ПлательщикНаправление",
        "ПлательщикНаправление_Type",
    }
    assert payload["Автор"] == "ИИ 1С"
    assert payload["ПлательщикНаправление"] == resolve_payer_direction("НП", "КС")


def test_retry_erp_task_missing_xml_does_not_call_integration() -> None:
    row = _info_error_row(with_xml=False)
    integration = MagicMock()

    with _mock_retry_deps(row=row, integration=integration, email=_info_sample_email()):
        task = retry_erp_task
        task.push_request(retries=0, max_retries=5)
        try:
            result = task(row.message_id)
        finally:
            task.pop_request()

    assert result == {"ok": False, "reason": "missing_xml_document"}
    integration.create_incoming_correspondence.assert_not_called()
    assert row.status == ProcessingStatus.ERROR.value
    assert row.human_review is True


def test_retry_erp_task_max_retries_exceeded() -> None:
    row = _error_row(erp_retry_count=5)
    integration = MagicMock()

    with _mock_retry_deps(row=row, integration=integration):
        task = retry_erp_task
        task.push_request(retries=0, max_retries=5)
        try:
            result = task(row.message_id)
        finally:
            task.pop_request()

    assert result["ok"] is False
    assert result["reason"] == "max_retries_exceeded"
    integration.create_incoming_correspondence.assert_not_called()


@contextmanager
def _mock_api_repo(row: EmailMessageRow):
    repo = MagicMock()
    repo.get_by_id.return_value = row
    session = MagicMock()
    session_factory = MagicMock()
    session_factory.return_value.__enter__.return_value = session
    with patch("agent_pochta.api.app.get_session_factory", lambda: session_factory), patch(
        "agent_pochta.api.app.EmailRepository",
        lambda _s: repo,
    ):
        yield repo, session


def test_api_retry_erp_schedules_celery_task() -> None:
    row = _error_row(erp_retry_count=5)
    celery_task = MagicMock()
    celery_task.delay.return_value = MagicMock(id="task-123")

    with _mock_api_repo(row) as (_repo, session), patch("agent_pochta.api.app.retry_erp_task", celery_task):
        client = TestClient(app)
        response = client.post(f"/api/v1/email-messages/{row.id}/retry-erp")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "erp_retry_scheduled"
    assert body["message_id"] == ERROR_CASE_MESSAGE_ID
    assert body["task_id"] == "task-123"
    assert row.erp_retry_count == 0
    assert row.human_review is False
    session.commit.assert_called_once()
    celery_task.delay.assert_called_once_with(ERROR_CASE_MESSAGE_ID)
