"""Тесты прикрепления файлов к Document_ТД_ВходящаяКорреспонденция через OData."""

from __future__ import annotations

from unittest.mock import MagicMock

from datetime import datetime, timezone

import pytest

from agent_pochta.services.odata_attached_file import (
    AttachedFileError,
    AttachedFileInput,
    attach_file_to_incoming_document,
    build_attached_file_payload,
    format_attached_file_created_at,
    format_attached_file_modified_universal,
    resolve_stream_content_type,
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


def test_build_attached_file_payload_base64_mode_includes_binary_by_default():
    entity, payload = build_attached_file_payload(
        document_ref_key=DOC_KEY,
        file_input=AttachedFileInput(filename="scan.pdf", content=b"%PDF-1.4"),
    )
    assert entity == "Catalog_ТД_ВходящаяКорреспонденцияПрисоединенныеФайлы"
    assert payload["Description"] == "scan"
    assert payload["Расширение"] == "pdf"
    assert payload["ВладелецФайла_Key"] == DOC_KEY
    assert payload["ТипХраненияФайла"] == "ВИнформационнойБазе"
    assert "Том_Key" not in payload
    assert payload["ФайлХранилище_Base64Data"]
    assert payload["ФайлХранилище_Type"] == "application/octet-stream"
    assert payload["Размер"] == 8
    assert payload["ДатаСоздания"]
    assert payload["ДатаМодификацииУниверсальная"]
    assert not str(payload["ДатаСоздания"]).startswith("0001")


def test_build_attached_file_payload_uses_explicit_processed_at():
    ts = datetime(2026, 7, 23, 10, 30, 0, tzinfo=timezone.utc)
    _, payload = build_attached_file_payload(
        document_ref_key=DOC_KEY,
        file_input=AttachedFileInput(
            filename="Входящее_письмо.eml",
            content=b"From: a@b.com\r\n\r\n",
            processed_at=ts,
        ),
    )
    assert payload["ДатаСоздания"] == format_attached_file_created_at(ts)
    assert payload["ДатаМодификацииУниверсальная"] == format_attached_file_modified_universal(ts)


def test_build_attached_file_payload_stream_mode_excludes_binary():
    entity, payload = build_attached_file_payload(
        document_ref_key=DOC_KEY,
        file_input=AttachedFileInput(filename="scan.pdf", content=b"%PDF-1.4"),
        field_map={
            "entity": "Catalog_ТД_ВходящаяКорреспонденцияПрисоединенныеФайлы",
            "fields": {
                "name": "Description",
                "extension": "Расширение",
                "owner_key": "ВладелецФайла_Key",
                "storage_binary": "ФайлХранилище_Base64Data",
                "storage_binary_type": "ФайлХранилище_Type",
                "storage_kind": "ТипХраненияФайла",
                "size": "Размер",
            },
            "defaults": {
                "storage_kind": "ВИнформационнойБазе",
                "upload_binary_via_stream": True,
            },
        },
    )
    assert entity == "Catalog_ТД_ВходящаяКорреспонденцияПрисоединенныеФайлы"
    assert payload["ТипХраненияФайла"] == "ВИнформационнойБазе"
    assert "ФайлХранилище_Base64Data" not in payload
    assert "ФайлХранилище_Type" not in payload


def test_build_attached_file_payload_can_include_inline_binary():
    entity, payload = build_attached_file_payload(
        document_ref_key=DOC_KEY,
        file_input=AttachedFileInput(filename="scan.pdf", content=b"%PDF-1.4"),
        include_binary=True,
    )
    assert payload["ФайлХранилище_Base64Data"]
    assert payload["ФайлХранилище_Type"] == "application/octet-stream"


def test_build_attached_file_payload_sets_volume_key_from_defaults():
    _, payload = build_attached_file_payload(
        document_ref_key=DOC_KEY,
        file_input=AttachedFileInput(filename="scan.pdf", content=b"data"),
        include_binary=True,
        field_map={
            "entity": "Catalog_ТД_ВходящаяКорреспонденцияПрисоединенныеФайлы",
            "fields": {
                "name": "Description",
                "extension": "Расширение",
                "owner_key": "ВладелецФайла_Key",
                "volume_key": "Том_Key",
                "size": "Размер",
                "storage_kind": "ТипХраненияФайла",
            },
            "defaults": {
                "volume_key": "21886495-364e-11ea-82f2-ac1f6b05524c",
                "storage_kind": "ВТомахНаДиске",
                "upload_binary_via_stream": False,
            },
        },
    )
    assert payload["Том_Key"] == "21886495-364e-11ea-82f2-ac1f6b05524c"
    assert payload["ТипХраненияФайла"] == "ВТомахНаДиске"


def test_stream_mode_skips_volume_key_even_if_configured():
    _, payload = build_attached_file_payload(
        document_ref_key=DOC_KEY,
        file_input=AttachedFileInput(filename="scan.pdf", content=b"data"),
        field_map={
            "entity": "Catalog_ТД_ВходящаяКорреспонденцияПрисоединенныеФайлы",
            "fields": {
                "name": "Description",
                "extension": "Расширение",
                "owner_key": "ВладелецФайла_Key",
                "volume_key": "Том_Key",
                "storage_kind": "ТипХраненияФайла",
                "size": "Размер",
            },
            "defaults": {
                "volume_key": "21886495-364e-11ea-82f2-ac1f6b05524c",
                "storage_kind": "ВТомахНаДиске",
                "upload_binary_via_stream": True,
            },
        },
    )
    assert payload["ТипХраненияФайла"] == "ВИнформационнойБазе"
    assert "Том_Key" not in payload


def test_resolve_stream_content_type_for_eml():
    assert resolve_stream_content_type("Входящее_письмо.eml") == "message/rfc822"
    assert resolve_stream_content_type("scan.pdf") == "application/octet-stream"


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
    _entity, payload = client.create_entity.call_args[0]
    assert payload["ФайлХранилище_Base64Data"]
    client.put_entity_stream.assert_not_called()


def test_attach_file_stream_mode_uses_put():
    client = MagicMock()
    client.get_by_key.return_value = {"Ref_Key": DOC_KEY}
    client.create_entity.return_value = {"Ref_Key": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"}

    attach_file_to_incoming_document(
        client,
        document_ref_key=DOC_KEY,
        file_input=AttachedFileInput(filename="a.pdf", content=b"data"),
        field_map={
            "entity": "Catalog_ТД_ВходящаяКорреспонденцияПрисоединенныеФайлы",
            "owner_document_entity": "Document_ТД_ВходящаяКорреспонденция",
            "fields": {
                "name": "Description",
                "extension": "Расширение",
                "owner_key": "ВладелецФайла_Key",
                "storage_binary": "ФайлХранилище_Base64Data",
                "storage_binary_type": "ФайлХранилище_Type",
                "storage_stream": "ФайлХранилище",
                "storage_kind": "ТипХраненияФайла",
                "size": "Размер",
            },
            "defaults": {
                "storage_kind": "ВИнформационнойБазе",
                "upload_binary_via_stream": True,
            },
        },
    )

    client.put_entity_stream.assert_called_once_with(
        "Catalog_ТД_ВходящаяКорреспонденцияПрисоединенныеФайлы",
        "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        "ФайлХранилище",
        b"data",
        content_type="application/octet-stream",
    )


def test_attach_file_uploads_eml_with_rfc822_content_type():
    client = MagicMock()
    client.get_by_key.return_value = {"Ref_Key": DOC_KEY}
    client.create_entity.return_value = {"Ref_Key": "dddddddd-dddd-dddd-dddd-dddddddddddd"}

    attach_file_to_incoming_document(
        client,
        document_ref_key=DOC_KEY,
        file_input=AttachedFileInput(
            filename="Входящее_письмо.eml",
            content=b"From: a@b.com\r\nTo: c@d.com\r\nSubject: test\r\n\r\nbody\r\n",
        ),
    )

    _entity, payload = client.create_entity.call_args[0]
    assert payload["ФайлХранилище_Type"] == "application/octet-stream"
    client.put_entity_stream.assert_not_called()


def test_odata_integration_attach_files_delegates_to_client():
    service = ODataIntegrationService(
        "http://example/odata/standard.odata/",
        entity="Document_ТД_ВходящаяКорреспонденция",
    )
    service._client.get_by_key = MagicMock(return_value={"Ref_Key": DOC_KEY})
    service._client.create_entity = MagicMock(
        return_value={"Ref_Key": "cccccccc-cccc-cccc-cccc-cccccccccccc"}
    )
    service._client.put_entity_stream = MagicMock()

    out = service.attach_files_to_incoming_correspondence(
        document_ref_key=DOC_KEY,
        files=[AttachedFileInput(filename="doc.pdf", content=b"123")],
    )

    assert len(out) == 1
    assert out[0]["ref_key"] == "cccccccc-cccc-cccc-cccc-cccccccccccc"
    assert out[0]["filename"] == "doc.pdf"
    service._client.put_entity_stream.assert_not_called()
