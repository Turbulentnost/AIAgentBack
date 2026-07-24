"""Тесты синхронизации документа 1С после коррекции оператора."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from agent_pochta.db.models import EmailMessageRow
from agent_pochta.schemas import Attachment, EmailMessage, ProcessingStatus, Priority, RoutingResult
from agent_pochta.services.erp_attachments import (
    ERP_FULL_EMAIL_FILENAME,
    erp_email_upload_marker_names,
    merge_erp_attachment_lists,
    uploaded_erp_attachment_filenames,
)
from agent_pochta.services.erp_sync import merge_erp_sync_meta_into_payload, sync_existing_erp_document
from agent_pochta.services.odata_incoming_mapper import build_incoming_document_update_payload
from agent_pochta.services.odata_integration import ODataIntegrationService
from agent_pochta.workers.tasks import sync_erp_correction_task

ERROR_CASE_XML = (
    "<document>"
    "<organization>НП</organization>"
    "<theme>Запрос счёта</theme>"
    "<направление>КС</направление>"
    "<claim>false</claim>"
    "<partner>ООО «Лунда»</partner>"
    "<services><service><name>00-000155</name><process>исполнение</process></service></services>"
    "<email_sender>niani@lunda.ru</email_sender>"
    "<email_recipient>info@turbo-don.ru</email_recipient>"
    "<mail_datetime>2026-07-21 09:52:34</mail_datetime>"
    "<process>исполнение</process>"
    "</document>"
)


def _done_row(*, erp_attachments: list | None = None) -> EmailMessageRow:
    message_id = "<msg@example>#info@turbo-don.ru"
    payload = {
        "message_id": message_id,
        "mailbox": "info@turbo-don.ru",
        "sender_email": "niani@lunda.ru",
        "subject": "Счёт",
        "body_text": "Текст",
        "received_at": datetime(2026, 7, 21, 9, 52, 34, tzinfo=timezone.utc).isoformat(),
        "routing_recipient": "info@turbo-don.ru",
        "to": ["info@turbo-don.ru"],
        "xml_document": ERROR_CASE_XML,
        "attachments": [
            {"filename": "scan.pdf", "mime_type": "application/pdf", "size_bytes": 4}
        ],
    }
    if erp_attachments:
        payload["erp_attachments"] = erp_attachments
    return EmailMessageRow(
        id=uuid.uuid4(),
        message_id=message_id,
        received_at=datetime(2026, 7, 21, 9, 52, 34, tzinfo=timezone.utc).replace(tzinfo=None),
        mailbox="info@turbo-don.ru",
        sender_email="niani@lunda.ru",
        subject="Счёт",
        status=ProcessingStatus.DONE.value,
        department_id="00-000155",
        department_name="Отдел тендерных продаж",
        summary_ru="Краткий обзор",
        erp_document_number="ВК-000050",
        erp_task_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        attachments_count=1,
        raw_payload_json=json.dumps(payload, ensure_ascii=False),
    )


def _email() -> EmailMessage:
    return EmailMessage(
        message_id="<msg@example>#info@turbo-don.ru",
        mailbox="info@turbo-don.ru",
        sender_email="niani@lunda.ru",
        subject="Счёт",
        received_at=datetime(2026, 7, 21, 9, 52, 34, tzinfo=timezone.utc),
        to=["info@turbo-don.ru"],
        routing_recipient="info@turbo-don.ru",
        attachments=[
            Attachment(
                filename="scan.pdf",
                mime_type="application/pdf",
                size_bytes=4,
                content=b"1234",
            )
        ],
    )


def _routing() -> RoutingResult:
    return RoutingResult(
        department_id="00-000002",
        department_name="Бухгалтерия",
        confidence=1.0,
        reasoning="operator",
        priority=Priority.NORMAL,
    )


def test_build_incoming_document_update_payload_subset() -> None:
    payload = build_incoming_document_update_payload(
        _email(),
        _routing(),
        "Обзор",
        xml_document=ERROR_CASE_XML,
    )
    assert "Date" not in payload
    assert "ИсточникПоступления" not in payload
    assert payload["Кому"] == "00-000002"
    assert payload["Подразделение"] == "Бухгалтерия"
    assert payload["Партнер"] == "ООО «Лунда»"


def test_uploaded_erp_attachment_filenames() -> None:
    row = _done_row(erp_attachments=[{"filename": "scan.pdf", "ref_key": "x"}])
    assert uploaded_erp_attachment_filenames(row.raw_payload_json) == {"scan.pdf"}


def test_merge_erp_attachment_lists_deduplicates() -> None:
    merged = merge_erp_attachment_lists(
        [{"filename": "a.pdf", "ref_key": "1"}],
        [{"filename": "a.pdf", "ref_key": "2"}, {"filename": "b.pdf", "ref_key": "3"}],
    )
    names = {item["filename"] for item in merged}
    assert names == {"a.pdf", "b.pdf"}
    assert next(item for item in merged if item["filename"] == "a.pdf")["ref_key"] == "2"


def test_merge_erp_sync_meta_into_payload() -> None:
    raw = json.dumps({"erp_attachments": [{"filename": "old.pdf", "ref_key": "1"}]})
    merged = merge_erp_sync_meta_into_payload(
        raw,
        {
            "erp_last_sync_at": "2026-07-22T10:00:00",
            "erp_sync_errors": None,
            "erp_attachments": [{"filename": "new.pdf", "ref_key": "2"}],
        },
    )
    data = json.loads(merged or "{}")
    assert data["erp_last_sync_at"] == "2026-07-22T10:00:00"
    assert {item["filename"] for item in data["erp_attachments"]} == {"old.pdf", "new.pdf"}


def test_sync_existing_updates_and_skips_duplicate_attachments() -> None:
    row = _done_row(
        erp_attachments=[
            {"filename": "scan.pdf", "ref_key": "already"},
            {"filename": ERP_FULL_EMAIL_FILENAME, "ref_key": "eml-already"},
        ]
    )
    integration = MagicMock()
    integration.update_incoming_correspondence.return_value = {
        "updated": True,
        "erp_document_id": row.erp_task_id,
        "fields": {"Кому": "00-000002"},
    }

    result = sync_existing_erp_document(
        message_id=row.message_id,
        row=row,
        email=_email(),
        routing=_routing(),
        summary_ru=row.summary_ru or "",
        integration=integration,
        vault=None,
        xml_document=ERROR_CASE_XML,
    )

    assert result["ok"] is True
    assert result["updated"] is True
    assert result["attached_count"] == 0
    integration.update_incoming_correspondence.assert_called_once()
    integration.attach_files_to_incoming_correspondence.assert_not_called()


def test_sync_existing_force_reattach_eml() -> None:
    row = _done_row(
        erp_attachments=[
            {"filename": "scan.pdf", "ref_key": "already"},
            {"filename": ERP_FULL_EMAIL_FILENAME, "ref_key": "eml-broken", "size_bytes": 32815},
        ]
    )
    integration = MagicMock()
    integration.update_incoming_correspondence.return_value = {
        "updated": True,
        "erp_document_id": row.erp_task_id,
        "fields": {},
    }
    integration.attach_files_to_incoming_correspondence.return_value = [
        {"ref_key": "eml-new", "filename": "ВК-000050.msg", "size_bytes": 1000},
    ]

    result = sync_existing_erp_document(
        message_id=row.message_id,
        row=row,
        email=_email(),
        routing=_routing(),
        summary_ru=row.summary_ru or "",
        integration=integration,
        vault=None,
        xml_document=ERROR_CASE_XML,
        force_reattach_filenames=erp_email_upload_marker_names(row.erp_document_number),
    )

    assert result["ok"] is True
    assert result["attached_count"] == 1
    integration.attach_files_to_incoming_correspondence.assert_called_once()
    files = integration.attach_files_to_incoming_correspondence.call_args.kwargs["files"]
    assert any(item.filename == "ВК-000050.msg" for item in files)
    payload = json.loads(result["raw_payload_json"] or row.raw_payload_json or "{}")
    assert ERP_FULL_EMAIL_FILENAME not in {
        item.get("filename") for item in payload.get("erp_attachments", [])
    }


def test_odata_integration_update_calls_patch() -> None:
    service = ODataIntegrationService(
        "http://1c.local/odata/standard.odata",
        entity="Document_ТД_ВходящаяКорреспонденция",
    )
    with patch.object(service._client, "patch_entity") as patch_mock:
        result = service.update_incoming_correspondence(
            "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            _email(),
            _routing(),
            "Обзор",
            xml_document=ERROR_CASE_XML,
        )
    assert result["updated"] is True
    patch_mock.assert_called_once()
    payload = patch_mock.call_args[0][2]
    assert payload["Кому"] == "00-000002"


def test_sync_erp_correction_task_runs_sync() -> None:
    row = _done_row()
    session = MagicMock()
    repo = MagicMock()
    repo.get_by_message_id.return_value = row
    repo.load_email_from_row.return_value = _email()
    repo.build_routing_from_row.return_value = _routing()
    session_factory = MagicMock()
    session_factory.return_value.__enter__.return_value = session
    container = MagicMock()

    with patch("agent_pochta.workers.tasks.get_session_factory", lambda: session_factory), patch(
        "agent_pochta.workers.tasks.EmailRepository",
        lambda _s: repo,
    ), patch(
        "agent_pochta.workers.runtime.get_worker_container",
        lambda: container,
    ), patch(
        "agent_pochta.workers.tasks._sync_existing_erp_document",
        return_value={"ok": True, "updated": True},
    ) as sync_mock:
        result = sync_erp_correction_task(row.message_id)

    assert result["ok"] is True
    sync_mock.assert_called_once()
