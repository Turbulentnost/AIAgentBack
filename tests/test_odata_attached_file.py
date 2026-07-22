"""Тесты прикрепления файлов к Document_ТД_ВходящаяКорреспонденция через OData."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agent_pochta.services.odata_attached_file import (
    AttachedFileError,
    AttachedFileInput,
    attach_file_to_incoming_document,
    build_attached_file_payload,
    split_filename,
)
from agent_pochta.services.odata_integration import ODataIntegrationService

DOC_KEY = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def test_split_filename_pdf():
    assert split_filename("Документ.pdf") == ("Документ", "pdf")
    assert split_filename(r"C:\temp\scan.PDF") == ("scan", "pdf")


def test_split_filename_rejects_empty():
    with pytest.raises(AttachedFileError):
        split_filename("")
    with pytest.raises(AttachedFileError):
        split_filename(".pdf")


def test_build_attached_file_payload_contains_owner_and_binary():
    entity, payload = build_attached_file_payload(
        document_ref_key=DOC_KEY,
        file_input=AttachedFileInput(filename="scan.pdf", content=b"%PDF-1.4"),
    )
    assert entity == "Catalog_ТД_ВходящаяКорреспонденцияПрисоединенныеФайлы"
    assert payload["Description"] == "scan"
    assert payload["Расширение"] == "pdf"
    assert payload["ВладелецФайла_Key"] == DOC_KEY
    assert payload["ФайлХранилище_Base64Data"]
    assert payload["ФайлХранилище_Type"] == "application/octet-stream"
    assert "ВладелецФайла" not in payload
    assert payload["Размер"] == 8


def test_attach_file_validates_empty_document_ref():
    client = MagicMock()
    with pytest.raises(AttachedFileError, match="не заполнена"):
        attach_file_to_incoming_document(
            client,
            document_ref_key="",
            file_input=AttachedFileInput(filename="a.pdf", content=b"x"),
        )


def test_attach_file_validates_empty_content():
    client = MagicMock()
    with pytest.raises(AttachedFileError, match="Пустой файл"):
        attach_file_to_incoming_document(
            client,
            document_ref_key=DOC_KEY,
            file_input=AttachedFileInput(filename="a.pdf", content=b""),
        )


def test_attach_file_checks_owner_exists():
    client = MagicMock()
    client.get_by_key.return_value = None
    with pytest.raises(AttachedFileError, match="не найден"):
        attach_file_to_incoming_document(
            client,
            document_ref_key=DOC_KEY,
            file_input=AttachedFileInput(filename="a.pdf", content=b"data"),
        )
    client.create_entity.assert_not_called()


def test_attach_file_posts_to_catalog():
    client = MagicMock()
    client.get_by_key.return_value = {"Ref_Key": DOC_KEY}
    client.create_entity.return_value = {"Ref_Key": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"}

    result = attach_file_to_incoming_document(
        client,
        document_ref_key=DOC_KEY,
        file_input=AttachedFileInput(filename="a.pdf", content=b"data"),
    )

    assert result.ref_key == "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    assert result.filename == "a"
    assert result.extension == "pdf"
    client.create_entity.assert_called_once()


def test_odata_integration_attach_files_delegates_to_client():
    service = ODataIntegrationService(
        "http://example/odata/standard.odata/",
        entity="Document_ТД_ВходящаяКорреспонденция",
    )
    service._client.get_by_key = MagicMock(return_value={"Ref_Key": DOC_KEY})
    service._client.create_entity = MagicMock(
        return_value={"Ref_Key": "cccccccc-cccc-cccc-cccc-cccccccccccc"}
    )

    out = service.attach_files_to_incoming_correspondence(
        document_ref_key=DOC_KEY,
        files=[AttachedFileInput(filename="doc.pdf", content=b"123")],
    )

    assert len(out) == 1
    assert out[0]["ref_key"] == "cccccccc-cccc-cccc-cccc-cccccccccccc"
    assert out[0]["filename"] == "doc"
