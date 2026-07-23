"""Тесты прикрепления вложений письма к документу 1С после создания."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from agent_pochta.attachments.cache import clear_attachment_cache
from agent_pochta.schemas import Attachment, EmailMessage
from agent_pochta.services.erp_attachments import (
    ERP_FULL_EMAIL_FILENAME,
    attach_email_files_to_document,
    cache_email_attachment_bytes,
    ensure_attachment_bytes_for_erp,
    ensure_full_email_bytes_for_erp,
    erp_attachment_filename,
    erp_email_upload_marker_names,
    erp_full_email_filename,
)
from agent_pochta.services.integration_service import StubIntegrationService
from agent_pochta.services.odata_integration import ODataIntegrationService

DOC_KEY = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
ERP_DOC_NUMBER = "НП00-003877"
DOC_NUMBER = "НП00-003877"


def _email_with_attachment(*, content: bytes | None = b"pdf-bytes") -> EmailMessage:
    payload = content if content is not None else b""
    return EmailMessage(
        message_id="<erp-attach@test>",
        mailbox="info@turbo-don.ru",
        sender_email="vendor@example.com",
        subject="Скан",
        received_at=datetime.now(timezone.utc),
        attachments=[
            Attachment(
                filename="scan.pdf",
                mime_type="application/pdf",
                size_bytes=max(len(payload), 4),
                content=content,
            )
        ],
    )


def _email_without_attachments() -> EmailMessage:
    return EmailMessage(
        message_id="<erp-no-att@test>",
        mailbox="info@turbo-don.ru",
        sender_email="vendor@example.com",
        subject="Только текст",
        body_text="Текст письма без файлов.",
        received_at=datetime.now(timezone.utc),
    )


def test_attach_email_files_to_document_uses_integration():
    integration = StubIntegrationService()
    email = _email_with_attachment()

    result = attach_email_files_to_document(
        integration,
        document_ref_key=DOC_KEY,
        email=email,
        erp_document_number=DOC_NUMBER,
    )

    assert len(result) == 1
    assert result[0]["filename"] == f"{DOC_NUMBER}.eml"
    assert "scan.pdf" not in {item["filename"] for item in result}


def test_attach_email_files_to_document_fetches_missing_content():
    integration = StubIntegrationService()
    email = _email_with_attachment(content=None)
    vault = MagicMock()

    with patch(
        "agent_pochta.services.erp_attachments.ensure_attachment_bytes_for_erp",
        side_effect=lambda target, _vault: setattr(
            target.attachments[0], "content", b"restored"
        )
        or 1,
    ) as ensure_mock:
        result = attach_email_files_to_document(
            integration,
            document_ref_key=DOC_KEY,
            email=email,
            vault=vault,
            erp_document_number=DOC_NUMBER,
        )

    ensure_mock.assert_called_once_with(email, vault)
    assert len(result) == 1
    assert result[0]["filename"] == f"{DOC_NUMBER}.eml"


def test_attach_email_files_attaches_full_email_without_file_attachments():
    integration = StubIntegrationService()
    email = _email_without_attachments()

    result = attach_email_files_to_document(
        integration,
        document_ref_key=DOC_KEY,
        email=email,
        erp_document_number=DOC_NUMBER,
    )

    assert len(result) == 1
    assert result[0]["filename"] == f"{DOC_NUMBER}.eml"
    assert result[0]["size_bytes"] > 0


def test_attach_email_files_still_attaches_eml_when_file_fetch_fails():
    integration = StubIntegrationService()
    email = _email_with_attachment(content=None)
    vault = MagicMock()

    with patch(
        "agent_pochta.services.erp_attachments.ensure_attachment_bytes_for_erp",
        return_value=0,
    ):
        result = attach_email_files_to_document(
            integration,
            document_ref_key=DOC_KEY,
            email=email,
            vault=vault,
            erp_document_number=DOC_NUMBER,
        )

    assert len(result) == 1
    assert result[0]["filename"] == f"{DOC_NUMBER}.eml"


def test_ensure_full_email_bytes_prefers_imap_rfc822():
    clear_attachment_cache()
    email = _email_without_attachments()
    vault = MagicMock()

    with patch(
        "agent_pochta.services.erp_attachments._fetch_full_email_bytes_from_imap",
        return_value=b"raw-rfc822-bytes",
    ) as fetch_mock:
        content = ensure_full_email_bytes_for_erp(email, vault)

    fetch_mock.assert_called_once_with(email, vault)
    assert content == b"raw-rfc822-bytes"


def test_ensure_full_email_bytes_builds_synthetic_when_imap_missing():
    from email import message_from_bytes
    from email.header import decode_header

    def decoded_header(value: str | None) -> str:
        chunks: list[str] = []
        for chunk, charset in decode_header(value or ""):
            if isinstance(chunk, bytes):
                chunks.append(chunk.decode(charset or "utf-8", errors="replace"))
            else:
                chunks.append(chunk)
        return "".join(chunks)

    clear_attachment_cache()
    email = _email_without_attachments()

    content = ensure_full_email_bytes_for_erp(email, vault=None)

    assert b"Message-ID" in content
    assert email.body_text.encode("utf-8") in content
    assert b"\r\n" in content
    parsed = message_from_bytes(content)
    assert decoded_header(parsed["Subject"]) == email.subject
    assert parsed.get_content_type().startswith("multipart/") or parsed.get_content_type() == "text/plain"


def test_synthetic_eml_includes_headers_for_outlook():
    from email import message_from_bytes
    from email.header import decode_header

    from agent_pochta.services.erp_attachments import _build_synthetic_eml_bytes

    def decoded_header(value: str | None) -> str:
        chunks: list[str] = []
        for chunk, charset in decode_header(value or ""):
            if isinstance(chunk, bytes):
                chunks.append(chunk.decode(charset or "utf-8", errors="replace"))
            else:
                chunks.append(chunk)
        return "".join(chunks)

    email = _email_without_attachments()
    email.sender_name = "Отправитель"
    email.to = ["recipient@example.com"]
    email.subject = "Тестовая тема"

    content = _build_synthetic_eml_bytes(email)
    parsed = message_from_bytes(content)

    assert parsed["From"]
    assert parsed["To"] == "recipient@example.com"
    assert decoded_header(parsed["Subject"]) == "Тестовая тема"
    assert parsed["Date"]
    assert parsed["MIME-Version"] == "1.0"


def test_erp_full_email_filename_uses_document_number():
    assert erp_full_email_filename(erp_document_number=DOC_NUMBER) == f"{DOC_NUMBER}.eml"


def test_erp_email_upload_marker_names_includes_legacy_and_eml():
    names = erp_email_upload_marker_names(DOC_NUMBER)
    assert f"{DOC_NUMBER}.eml" in names
    assert ERP_FULL_EMAIL_FILENAME in names


def test_collect_erp_upload_files_keeps_eml_bytes():
    from agent_pochta.services.erp_attachments import _collect_erp_upload_files

    email = _email_without_attachments()
    eml = ensure_full_email_bytes_for_erp(email, vault=None)
    files = _collect_erp_upload_files(
        email,
        full_email_bytes=eml,
        erp_document_number=DOC_NUMBER,
    )
    assert len(files) == 1
    assert files[0].filename == f"{DOC_NUMBER}.eml"
    assert files[0].content == eml


def test_attach_email_files_skips_when_odata_attach_disabled():
    service = ODataIntegrationService(
        "http://example/odata/standard.odata/",
        entity="Document_ТД_ВходящаяКорреспонденция",
        attach_files_enabled=False,
    )
    email = _email_with_attachment()

    result = attach_email_files_to_document(
        service,
        document_ref_key=DOC_KEY,
        email=email,
    )

    assert result == []


def test_ensure_attachment_bytes_for_erp_restores_from_cache():
    email = _email_with_attachment(content=None)
    vault = MagicMock()
    cached = MagicMock(content=b"cached-bytes", mime_type="application/pdf")

    with patch(
        "agent_pochta.services.erp_attachments.get_cached_attachment",
        return_value=cached,
    ), patch(
        "agent_pochta.services.erp_attachments.ensure_attachments_from_imap",
    ) as imap_mock:
        restored = ensure_attachment_bytes_for_erp(email, vault)

    assert restored == 1
    assert email.attachments[0].content == b"cached-bytes"
    imap_mock.assert_not_called()


def test_ensure_attachment_bytes_for_erp_falls_back_to_partial_imap():
    email = _email_with_attachment(content=None)
    vault = MagicMock()

    with patch(
        "agent_pochta.services.erp_attachments.get_cached_attachment",
        return_value=None,
    ), patch(
        "agent_pochta.services.erp_attachments.ensure_attachments_from_imap",
        return_value=0,
    ), patch(
        "agent_pochta.services.erp_attachments.resolve_imap_credentials",
        return_value=MagicMock(),
    ), patch(
        "agent_pochta.services.erp_attachments.ImapMailboxClient",
    ) as client_cls:
        client = client_cls.return_value
        client.fetch_attachment_bytes.return_value = (
            b"partial-bytes",
            "application/pdf",
            "scan.pdf",
        )
        restored = ensure_attachment_bytes_for_erp(email, vault)

    assert restored == 1
    assert email.attachments[0].content == b"partial-bytes"
    client.fetch_attachment_bytes.assert_called_once()


def test_erp_attachment_filename_adds_extension_from_mime():
    att = Attachment(
        filename="scan",
        mime_type="application/pdf",
        size_bytes=100,
        content=b"x",
    )
    assert erp_attachment_filename(att) == "scan.pdf"


def test_cache_email_attachment_bytes_stores_in_cache():
    email = _email_with_attachment()
    assert cache_email_attachment_bytes(email) == 1
    empty = _email_with_attachment(content=None)
    restored = ensure_attachment_bytes_for_erp(empty, MagicMock())
    assert restored >= 1
    assert empty.attachments[0].content == b"pdf-bytes"


def test_existing_erp_document_ref_key_from_row():
    from agent_pochta.services.erp_attachments import existing_erp_document_ref_key

    row = MagicMock(erp_task_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
    assert existing_erp_document_ref_key(row) == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

    assert existing_erp_document_ref_key(MagicMock(erp_task_id="SKIP-ERP")) is None
    assert existing_erp_document_ref_key(MagicMock(erp_task_id=None)) is None


def test_erp_attachments_already_uploaded():
    from agent_pochta.services.erp_attachments import erp_attachments_already_uploaded

    payload = json.dumps({"erp_attachments": [{"ref_key": "x"}]})
    assert erp_attachments_already_uploaded(payload) is True
    assert erp_attachments_already_uploaded(json.dumps({})) is False


def test_uploaded_erp_attachment_filenames_includes_extension():
    from agent_pochta.services.erp_attachments import uploaded_erp_attachment_filenames

    payload = json.dumps(
        {
            "erp_attachments": [
                {"filename": "image001", "extension": "png", "ref_key": "x"},
            ]
        }
    )
    assert uploaded_erp_attachment_filenames(payload) == {"image001", "image001.png"}

