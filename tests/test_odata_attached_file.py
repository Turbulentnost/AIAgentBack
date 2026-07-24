"""Тесты прикрепления файлов к Document_ТД_ВходящаяКорреспонденция через OData."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from agent_pochta.services.odata_attached_file import (
    AttachedFileError,
    AttachedFileInput,
    attach_file_to_incoming_document,
    build_attached_file_payload,
    delete_attached_files_for_document,
    format_attached_file_created_at,
    format_attached_file_modified_universal,
    format_volume_file_path,
    list_attached_files_for_document,
    now_attached_file_processed_at,
    read_attached_file_storage_bytes,
    release_attached_file_edit_lock,
    resolve_attached_file_storage_mode,
    resolve_stream_content_type,
    split_filename,
    verify_attached_file_reference_fields,
    verify_attached_file_storage,
)

_VOLUME_FIELD_MAP = {
    "entity": "Catalog_ТД_ВходящаяКорреспонденцияПрисоединенныеФайлы",
    "owner_document_entity": "Document_ТД_ВходящаяКорреспонденция",
    "fields": {
        "name": "Description",
        "extension": "Расширение",
        "owner_key": "ВладелецФайла_Key",
        "volume_key": "Том_Key",
        "file_path": "ПутьКФайлу",
        "storage_binary": "ФайлХранилище_Base64Data",
        "storage_binary_type": "ФайлХранилище_Type",
        "storage_stream": "ФайлХранилище",
        "storage_kind": "ТипХраненияФайла",
        "size": "Размер",
        "created_at": "ДатаСоздания",
        "modified_at": "ДатаМодификацииУниверсальная",
        "loan_date": "ДатаЗаема",
        "author_key": "Автор_Key",
        "modified_by_key": "Изменил_Key",
        "edit_lock_key": "Редактирует_Key",
        "comment": "Описание",
        "deletion_mark": "DeletionMark",
        "is_folder": "IsFolder",
        "parent_key": "Parent_Key",
        "image_index": "ИндексКартинки",
        "store_versions": "ХранитьВерсии",
        "signed_ep": "ПодписанЭП",
        "encrypted": "Зашифрован",
        "text_extraction_status": "СтатусИзвлеченияТекста",
        "text_storage_type": "ТекстХранилище_Type",
        "text_storage_binary": "ТекстХранилище_Base64Data",
    },
    "defaults": {
        "storage_mode": "volume",
        "storage_kind": "ВТомахНаДиске",
        "volume_key": "21886495-364e-11ea-82f2-ac1f6b05524c",
        "volume_binary_type": "application/xml+xdto",
        "storage_binary_type": "application/octet-stream",
        "text_storage_type": "application/xml+xdto",
        "upload_binary_via_stream": False,
        "loan_date": "0001-01-01T00:00:00",
        "image_index": "0",
        "comment": "",
        "text_extraction_status": "",
        "text_storage_binary": "",
        "deletion_mark": False,
        "is_folder": False,
        "store_versions": False,
        "signed_ep": False,
        "encrypted": False,
    },
}

_DATABASE_FIELD_MAP = {
    "entity": "Catalog_ТД_ВходящаяКорреспонденцияПрисоединенныеФайлы",
    "owner_document_entity": "Document_ТД_ВходящаяКорреспонденция",
    "fields": {
        "name": "Description",
        "extension": "Расширение",
        "owner_key": "ВладелецФайла_Key",
        "volume_key": "Том_Key",
        "file_path": "ПутьКФайлу",
        "storage_binary": "ФайлХранилище_Base64Data",
        "storage_binary_type": "ФайлХранилище_Type",
        "storage_stream": "ФайлХранилище",
        "storage_kind": "ТипХраненияФайла",
        "size": "Размер",
        "created_at": "ДатаСоздания",
        "modified_at": "ДатаМодификацииУниверсальная",
        "loan_date": "ДатаЗаема",
        "author_key": "Автор_Key",
        "modified_by_key": "Изменил_Key",
        "edit_lock_key": "Редактирует_Key",
        "comment": "Описание",
        "deletion_mark": "DeletionMark",
        "is_folder": "IsFolder",
        "parent_key": "Parent_Key",
        "image_index": "ИндексКартинки",
        "store_versions": "ХранитьВерсии",
        "signed_ep": "ПодписанЭП",
        "encrypted": "Зашифрован",
        "text_extraction_status": "СтатусИзвлеченияТекста",
        "text_storage_type": "ТекстХранилище_Type",
        "text_storage_binary": "ТекстХранилище_Base64Data",
    },
    "defaults": {
        "storage_mode": "database",
        "storage_kind": "ВИнформационнойБазе",
        "storage_binary_type": "application/octet-stream",
        "text_storage_type": "application/xml+xdto",
        "upload_binary_via_stream": False,
        "loan_date": "0001-01-01T00:00:00",
        "image_index": "0",
        "comment": "",
        "text_extraction_status": "",
        "text_storage_binary": "",
        "deletion_mark": False,
        "is_folder": False,
        "store_versions": False,
        "signed_ep": False,
        "encrypted": False,
    },
}
from agent_pochta.services.odata_integration import (
    ODataIntegrationService,
    resolve_attached_file_author_key,
)

DOC_KEY = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
AUTHOR_KEY = "a5e55eea-3a0a-11f0-9679-6cb31113810c"


def test_split_filename_pdf():
    assert split_filename("Документ.pdf") == ("Документ", "pdf")
    assert split_filename(r"C:\temp\scan.PDF") == ("scan", "pdf")


def test_split_filename_rejects_empty():
    with pytest.raises(AttachedFileError):
        split_filename("")
    with pytest.raises(AttachedFileError):
        split_filename(".pdf")


def test_build_attached_file_payload_database_mode_by_default():
    ts = datetime(2026, 7, 24, 10, 30, 0, tzinfo=ZoneInfo("Europe/Moscow"))
    entity, payload = build_attached_file_payload(
        document_ref_key=DOC_KEY,
        file_input=AttachedFileInput(
            filename="АЛ00-000762.msg",
            content=b"\xd0\xcf\x11\xe0" + b"\x00" * 64,
            processed_at=ts,
        ),
    )
    assert entity == "Catalog_ТД_ВходящаяКорреспонденцияПрисоединенныеФайлы"
    assert payload["Description"] == "АЛ00-000762"
    assert payload["Расширение"] == "msg"
    assert payload["ВладелецФайла_Key"] == DOC_KEY
    assert payload["ТипХраненияФайла"] == "ВИнформационнойБазе"
    assert payload["Том_Key"] == "00000000-0000-0000-0000-000000000000"
    assert payload["ПутьКФайлу"] == ""
    assert payload["ФайлХранилище_Type"] == "application/octet-stream"
    assert payload["ФайлХранилище_Base64Data"]
    assert payload["Размер"] == 68
    assert payload["ДатаЗаема"] == "0001-01-01T00:00:00"
    assert "Изменил_Key" not in payload
    assert payload["ИндексКартинки"] == "0"
    assert payload["DeletionMark"] is False
    assert payload["ХранитьВерсии"] is False
    assert payload["ТекстХранилище_Type"] == "application/xml+xdto"
    assert "Редактирует_Key" not in payload


def test_build_attached_file_payload_database_mode_explicit():
    ts = datetime(2026, 7, 24, 10, 30, 0, tzinfo=ZoneInfo("Europe/Moscow"))
    entity, payload = build_attached_file_payload(
        document_ref_key=DOC_KEY,
        file_input=AttachedFileInput(
            filename="АЛ00-000762.msg",
            content=b"\xd0\xcf\x11\xe0" + b"\x00" * 64,
            processed_at=ts,
        ),
        field_map=_DATABASE_FIELD_MAP,
    )
    assert entity == "Catalog_ТД_ВходящаяКорреспонденцияПрисоединенныеФайлы"
    assert payload["ТипХраненияФайла"] == "ВИнформационнойБазе"
    assert payload["Том_Key"] == "00000000-0000-0000-0000-000000000000"
    assert payload["ПутьКФайлу"] == ""
    assert payload["ФайлХранилище_Type"] == "application/octet-stream"
    assert payload["ФайлХранилище_Base64Data"]


def test_build_attached_file_payload_volume_mode_explicit():
    ts = datetime(2026, 7, 24, 10, 30, 0, tzinfo=ZoneInfo("Europe/Moscow"))
    entity, payload = build_attached_file_payload(
        document_ref_key=DOC_KEY,
        file_input=AttachedFileInput(
            filename="АЛ00-000762.msg",
            content=b"\xd0\xcf\x11\xe0" + b"\x00" * 64,
            processed_at=ts,
        ),
        field_map=_VOLUME_FIELD_MAP,
    )
    assert entity == "Catalog_ТД_ВходящаяКорреспонденцияПрисоединенныеФайлы"
    assert payload["ТипХраненияФайла"] == "ВТомахНаДиске"
    assert payload["Том_Key"] == "21886495-364e-11ea-82f2-ac1f6b05524c"
    assert payload["ПутьКФайлу"] == "20260724\\АЛ00-000762.msg"
    assert payload["ФайлХранилище_Type"] == "application/xml+xdto"
    assert "ФайлХранилище_Base64Data" not in payload


def test_build_attached_file_payload_base64_mode_includes_binary_by_default():
    entity, payload = build_attached_file_payload(
        document_ref_key=DOC_KEY,
        file_input=AttachedFileInput(filename="scan.pdf", content=b"%PDF-1.4"),
        field_map=_DATABASE_FIELD_MAP,
    )
    assert entity == "Catalog_ТД_ВходящаяКорреспонденцияПрисоединенныеФайлы"
    assert payload["Description"] == "scan"
    assert payload["Расширение"] == "pdf"
    assert payload["ВладелецФайла_Key"] == DOC_KEY
    assert payload["ТипХраненияФайла"] == "ВИнформационнойБазе"
    assert payload["Том_Key"] == "00000000-0000-0000-0000-000000000000"
    assert payload["ПутьКФайлу"] == ""
    assert payload["ФайлХранилище_Base64Data"]
    assert payload["ФайлХранилище_Type"] == "application/octet-stream"
    assert payload["Размер"] == 8
    assert payload["ДатаСоздания"]
    assert payload["ДатаМодификацииУниверсальная"]
    assert not str(payload["ДатаСоздания"]).startswith("0001")


def test_format_volume_file_path_uses_msk_date():
    ts = datetime(2026, 7, 24, 1, 0, 0, tzinfo=timezone.utc)
    assert format_volume_file_path(ts, "АЛ00-000762.msg") == "20260724\\АЛ00-000762.msg"


def test_build_attached_file_payload_volume_path_matches_outlook_subject():
    ts = datetime(2026, 7, 24, 10, 30, 0, tzinfo=ZoneInfo("Europe/Moscow"))
    _, payload = build_attached_file_payload(
        document_ref_key=DOC_KEY,
        file_input=AttachedFileInput(
            filename="Заявка!.msg",
            content=b"\xd0\xcf\x11\xe0" + b"\x00" * 64,
            processed_at=ts,
        ),
        field_map=_VOLUME_FIELD_MAP,
    )
    assert payload["Description"] == "Заявка!"
    assert payload["ПутьКФайлу"] == "20260724\\Заявка!.msg"


def test_resolve_attached_file_storage_mode():
    assert resolve_attached_file_storage_mode({"storage_mode": "volume"}) == "volume"
    assert resolve_attached_file_storage_mode({"storage_mode": "database"}) == "database"
    assert resolve_attached_file_storage_mode({"storage_kind": "ВИнформационнойБазе"}) == "database"


def test_build_attached_file_payload_sets_modified_by_key_from_author():
    _, payload = build_attached_file_payload(
        document_ref_key=DOC_KEY,
        file_input=AttachedFileInput(
            filename="АЛ00-000762.msg",
            content=b"msg-bytes",
            author_key=AUTHOR_KEY,
            edited_by_key=AUTHOR_KEY,
        ),
        field_map=_DATABASE_FIELD_MAP,
    )
    assert payload["Автор_Key"] == AUTHOR_KEY
    assert payload["Изменил_Key"] == AUTHOR_KEY
    assert "Редактирует_Key" not in payload


def test_build_attached_file_payload_uses_explicit_processed_at():
    ts = datetime(2026, 7, 23, 10, 30, 0, tzinfo=ZoneInfo("Europe/Moscow"))
    _, payload = build_attached_file_payload(
        document_ref_key=DOC_KEY,
        file_input=AttachedFileInput(
            filename="НП00-003877.eml",
            content=b"From: a@b.com\r\n\r\n",
            processed_at=ts,
            author_key=AUTHOR_KEY,
        ),
        field_map=_DATABASE_FIELD_MAP,
    )
    assert payload["Description"] == "НП00-003877"
    assert payload["ДатаСоздания"] == format_attached_file_created_at(ts)
    assert payload["ДатаМодификацииУниверсальная"] == format_attached_file_modified_universal(ts)
    assert payload["Автор_Key"] == AUTHOR_KEY
    assert "Редактирует_Key" not in payload


def test_build_attached_file_payload_defaults_to_msk_now(monkeypatch):
    fixed = datetime(2026, 7, 23, 10, 27, 0, tzinfo=ZoneInfo("Europe/Moscow"))

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed

    monkeypatch.setattr("agent_pochta.services.odata_attached_file.datetime", FixedDatetime)
    _, payload = build_attached_file_payload(
        document_ref_key=DOC_KEY,
        file_input=AttachedFileInput(filename="НП00-003877.eml", content=b"eml"),
        field_map=_DATABASE_FIELD_MAP,
    )
    assert payload["ДатаСоздания"] == "2026-07-23T10:27:00"


def test_resolve_attached_file_author_key_from_defaults():
    assert resolve_attached_file_author_key() == AUTHOR_KEY
    assert resolve_attached_file_author_key(explicit_key=AUTHOR_KEY) == AUTHOR_KEY


def test_resolve_attached_file_author_key_prefers_user_key(monkeypatch, tmp_path):
    defaults_path = tmp_path / "defaults.json"
    defaults_path.write_text(
        json.dumps(
            {
                "Пользователь_Key": "11111111-1111-1111-1111-111111111111",
                "Ответственный_Key": "22222222-2222-2222-2222-222222222222",
            }
        ),
        encoding="utf-8",
    )
    assert (
        resolve_attached_file_author_key(incoming_defaults_file=defaults_path)
        == "11111111-1111-1111-1111-111111111111"
    )


def test_odata_integration_attach_files_requires_author():
    service = ODataIntegrationService(
        "http://example/odata/standard.odata/",
        entity="Document_ТД_ВходящаяКорреспонденция",
        file_author_key="",
        incoming_defaults_file="__missing__.json",
    )
    with pytest.raises(AttachedFileError, match="Автор_Key"):
        service.attach_files_to_incoming_correspondence(
            document_ref_key=DOC_KEY,
            files=[AttachedFileInput(filename="НП00-003877.msg", content=b"123")],
        )


def test_build_attached_file_payload_stream_mode_excludes_binary():
    entity, payload = build_attached_file_payload(
        document_ref_key=DOC_KEY,
        file_input=AttachedFileInput(filename="scan.pdf", content=b"%PDF-1.4"),
        field_map={
            **_DATABASE_FIELD_MAP,
            "defaults": {
                **_DATABASE_FIELD_MAP["defaults"],
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
        field_map=_DATABASE_FIELD_MAP,
    )
    assert payload["ФайлХранилище_Base64Data"]
    assert payload["ФайлХранилище_Type"] == "application/octet-stream"


def test_build_attached_file_payload_sets_volume_key_from_defaults():
    _, payload = build_attached_file_payload(
        document_ref_key=DOC_KEY,
        file_input=AttachedFileInput(filename="scan.pdf", content=b"data"),
        include_binary=True,
        field_map={
            **_DATABASE_FIELD_MAP,
            "fields": {
                **_DATABASE_FIELD_MAP["fields"],
                "volume_key": "Том_Key",
            },
            "defaults": {
                **_DATABASE_FIELD_MAP["defaults"],
                "volume_key": "21886495-364e-11ea-82f2-ac1f6b05524c",
                "storage_kind": "ВТомахНаДиске",
                "storage_mode": "volume",
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
            **_DATABASE_FIELD_MAP,
            "fields": {
                **_DATABASE_FIELD_MAP["fields"],
                "volume_key": "Том_Key",
            },
            "defaults": {
                **_DATABASE_FIELD_MAP["defaults"],
                "volume_key": "21886495-364e-11ea-82f2-ac1f6b05524c",
                "storage_kind": "ВТомахНаДиске",
                "upload_binary_via_stream": True,
            },
        },
    )
    assert payload["ТипХраненияФайла"] == "ВИнформационнойБазе"
    assert payload["Том_Key"] == "00000000-0000-0000-0000-000000000000"


def test_resolve_stream_content_type_for_eml_and_msg():
    db_defaults = _DATABASE_FIELD_MAP["defaults"]
    assert (
        resolve_stream_content_type(
            "Входящее_письмо.eml",
            defaults=db_defaults,
            storage_mode="database",
        )
        == "message/rfc822"
    )
    assert (
        resolve_stream_content_type(
            "НП00-003877.msg",
            defaults=db_defaults,
            storage_mode="database",
        )
        == "application/vnd.ms-outlook"
    )
    assert (
        resolve_stream_content_type(
            "scan.pdf",
            defaults=db_defaults,
            storage_mode="database",
        )
        == "application/octet-stream"
    )
    assert (
        resolve_stream_content_type(
            "scan.pdf",
            defaults=_VOLUME_FIELD_MAP["defaults"],
            storage_mode="volume",
        )
        == "application/xml+xdto"
    )


def test_build_attached_file_payload_msg_volume_uses_xdto_type():
    _, payload = build_attached_file_payload(
        document_ref_key=DOC_KEY,
        file_input=AttachedFileInput(
            filename="НП00-003877.msg",
            content=b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 64,
        ),
        field_map=_VOLUME_FIELD_MAP,
    )
    assert payload["Description"] == "НП00-003877"
    assert payload["Расширение"] == "msg"
    assert payload["ФайлХранилище_Type"] == "application/xml+xdto"
    assert "ФайлХранилище_Base64Data" not in payload


def test_build_attached_file_payload_msg_uses_octet_stream_for_base64_post():
    _, payload = build_attached_file_payload(
        document_ref_key=DOC_KEY,
        file_input=AttachedFileInput(
            filename="НП00-003877.msg",
            content=b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 64,
        ),
        field_map=_DATABASE_FIELD_MAP,
    )
    assert payload["Description"] == "НП00-003877"
    assert payload["Расширение"] == "msg"
    assert payload["ФайлХранилище_Type"] == "application/octet-stream"


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


def test_attach_file_posts_to_catalog_volume_mode():
    client = MagicMock()
    client.get_by_key.return_value = {
        "Ref_Key": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        "Размер": 4,
        "ТипХраненияФайла": "ВТомахНаДиске",
        "Том_Key": "21886495-364e-11ea-82f2-ac1f6b05524c",
        "ПутьКФайлу": "20260724\\a.pdf",
        "Редактирует_Key": "00000000-0000-0000-0000-000000000000",
    }
    client.get_entity_stream.return_value = b""
    client.create_entity.return_value = {"Ref_Key": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"}

    result = attach_file_to_incoming_document(
        client,
        document_ref_key=DOC_KEY,
        file_input=AttachedFileInput(filename="a.pdf", content=b"data"),
        field_map=_VOLUME_FIELD_MAP,
    )

    assert result.ref_key == "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    assert result.filename == "a"
    assert result.extension == "pdf"
    client.create_entity.assert_called_once()
    _entity, payload = client.create_entity.call_args[0]
    assert "ФайлХранилище_Base64Data" not in payload
    assert payload["ПутьКФайлу"]
    client.put_entity_stream.assert_called_once_with(
        "Catalog_ТД_ВходящаяКорреспонденцияПрисоединенныеФайлы",
        "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        "ФайлХранилище",
        b"data",
        content_type="application/xml+xdto",
    )
    client.get_entity_stream.assert_called_once()


def test_attach_file_posts_to_catalog_database_mode():
    client = MagicMock()
    client.get_by_key.return_value = {"Ref_Key": DOC_KEY, "Размер": 4}
    client.get_entity_stream.return_value = b"data"
    client.create_entity.return_value = {"Ref_Key": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"}

    result = attach_file_to_incoming_document(
        client,
        document_ref_key=DOC_KEY,
        file_input=AttachedFileInput(filename="a.pdf", content=b"data"),
        field_map=_DATABASE_FIELD_MAP,
    )

    assert result.ref_key == "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    client.create_entity.assert_called_once()
    _entity, payload = client.create_entity.call_args[0]
    assert payload["ФайлХранилище_Base64Data"]
    client.put_entity_stream.assert_not_called()


def test_attach_file_stream_mode_uses_put():
    client = MagicMock()
    client.get_by_key.return_value = {"Ref_Key": DOC_KEY, "Размер": 4}
    client.get_entity_stream.return_value = b"data"
    client.create_entity.return_value = {"Ref_Key": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"}

    attach_file_to_incoming_document(
        client,
        document_ref_key=DOC_KEY,
        file_input=AttachedFileInput(filename="a.pdf", content=b"data"),
        field_map={
            **_DATABASE_FIELD_MAP,
            "defaults": {
                **_DATABASE_FIELD_MAP["defaults"],
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


def test_attach_file_uploads_eml_with_octet_stream_content_type():
    client = MagicMock()
    client.get_by_key.return_value = {"Ref_Key": DOC_KEY, "Размер": 51}
    client.get_entity_stream.return_value = (
        b"From: a@b.com\r\nTo: c@d.com\r\nSubject: test\r\n\r\nbody\r\n"
    )
    client.create_entity.return_value = {"Ref_Key": "dddddddd-dddd-dddd-dddd-dddddddddddd"}

    attach_file_to_incoming_document(
        client,
        document_ref_key=DOC_KEY,
        file_input=AttachedFileInput(
            filename="Входящее_письмо.eml",
            content=b"From: a@b.com\r\nTo: c@d.com\r\nSubject: test\r\n\r\nbody\r\n",
        ),
        field_map=_DATABASE_FIELD_MAP,
    )

    _entity, payload = client.create_entity.call_args[0]
    assert payload["ФайлХранилище_Type"] == "application/octet-stream"
    client.put_entity_stream.assert_not_called()


def test_odata_integration_attach_files_delegates_to_client():
    service = ODataIntegrationService(
        "http://example/odata/standard.odata/",
        file_author_key=AUTHOR_KEY,
        entity="Document_ТД_ВходящаяКорреспонденция",
    )
    service._client.get_by_key = MagicMock(
        return_value={
            "Ref_Key": DOC_KEY,
            "Размер": 3,
            "ТипХраненияФайла": "ВИнформационнойБазе",
            "Том_Key": "00000000-0000-0000-0000-000000000000",
            "ПутьКФайлу": "",
            "Редактирует_Key": "00000000-0000-0000-0000-000000000000",
            "Изменил_Key": AUTHOR_KEY,
            "Автор_Key": AUTHOR_KEY,
        }
    )
    service._client.get_entity_stream = MagicMock(return_value=b"123")
    service._client.create_entity = MagicMock(
        return_value={"Ref_Key": "cccccccc-cccc-cccc-cccc-cccccccccccc"}
    )
    service._client.put_entity_stream = MagicMock()
    service._client.patch_entity = MagicMock()

    out = service.attach_files_to_incoming_correspondence(
        document_ref_key=DOC_KEY,
        files=[AttachedFileInput(filename="НП00-003877.msg", content=b"123")],
    )

    assert len(out) == 1
    assert out[0]["ref_key"] == "cccccccc-cccc-cccc-cccc-cccccccccccc"
    assert out[0]["filename"] == "НП00-003877.msg"
    _entity, payload = service._client.create_entity.call_args[0]
    assert payload["Автор_Key"] == AUTHOR_KEY
    assert "Редактирует_Key" not in payload
    assert payload["Description"] == "НП00-003877"
    assert payload["ФайлХранилище_Base64Data"]
    assert payload["ТипХраненияФайла"] == "ВИнформационнойБазе"
    assert payload["ПутьКФайлу"] == ""
    service._client.patch_entity.assert_called()
    patch_calls = service._client.patch_entity.call_args_list
    assert any(
        call.args[2].get("Автор_Key") == AUTHOR_KEY for call in patch_calls
    )
    assert any(
        call.args[2].get("Редактирует_Key") == "00000000-0000-0000-0000-000000000000"
        for call in patch_calls
    )
    assert not any("Изменил_Key" in call.args[2] for call in patch_calls)
    service._client.put_entity_stream.assert_not_called()


def test_verify_attached_file_storage_accepts_volume_with_zero_stream():
    client = MagicMock()
    client.get_by_key.return_value = {
        "Ref_Key": DOC_KEY,
        "Размер": 100,
        "ТипХраненияФайла": "ВТомахНаДиске",
        "Том_Key": "21886495-364e-11ea-82f2-ac1f6b05524c",
        "ПутьКФайлу": "20260724\\АЛ00-000762.msg",
        "ФайлХранилище_Base64Data": "",
    }
    client.get_entity_stream.return_value = b""

    size = verify_attached_file_storage(
        client,
        entity="Catalog_ТД_ВходящаяКорреспонденцияПрисоединенныеФайлы",
        ref_key=DOC_KEY,
        expected_size=100,
    )
    assert size == 100


def test_verify_attached_file_storage_rejects_volume_without_path():
    client = MagicMock()
    client.get_by_key.return_value = {
        "Ref_Key": DOC_KEY,
        "Размер": 100,
        "ТипХраненияФайла": "ВТомахНаДиске",
        "Том_Key": "21886495-364e-11ea-82f2-ac1f6b05524c",
        "ПутьКФайлу": "",
    }
    client.get_entity_stream.return_value = b""

    with pytest.raises(AttachedFileError, match="ПутьКФайлу"):
        verify_attached_file_storage(
            client,
            entity="Catalog_ТД_ВходящаяКорреспонденцияПрисоединенныеФайлы",
            ref_key=DOC_KEY,
            expected_size=100,
        )


def test_verify_attached_file_storage_rejects_empty_stream_in_database_mode():
    client = MagicMock()
    client.get_by_key.return_value = {
        "Ref_Key": DOC_KEY,
        "Размер": 100,
        "ТипХраненияФайла": "ВИнформационнойБазе",
        "ФайлХранилище_Base64Data": "",
    }
    client.get_entity_stream.return_value = b""

    with pytest.raises(AttachedFileError, match="Пустое хранилище"):
        verify_attached_file_storage(
            client,
            entity="Catalog_ТД_ВходящаяКорреспонденцияПрисоединенныеФайлы",
            ref_key=DOC_KEY,
            expected_size=100,
            field_map=_DATABASE_FIELD_MAP,
        )


def test_read_attached_file_storage_bytes_falls_back_to_base64():
    client = MagicMock()
    client.get_entity_stream.return_value = b""
    client.get_by_key.return_value = {
        "ФайлХранилище_Base64Data": "aGVsbG8=",
    }

    content = read_attached_file_storage_bytes(
        client,
        entity="Catalog_ТД_ВходящаяКорреспонденцияПрисоединенныеФайлы",
        ref_key=DOC_KEY,
    )

    assert content == b"hello"


def test_list_attached_files_for_document_uses_fetch_filtered():
    client = MagicMock()
    client.fetch_filtered.return_value = [{"Ref_Key": "f1", "ВладелецФайла_Key": DOC_KEY}]
    rows = list_attached_files_for_document(client, document_ref_key=DOC_KEY)
    assert rows == [{"Ref_Key": "f1", "ВладелецФайла_Key": DOC_KEY}]
    client.fetch_filtered.assert_called_once()


def test_delete_attached_files_for_document():
    client = MagicMock()
    client.fetch_filtered.return_value = [
        {"Ref_Key": "f1", "ВладелецФайла_Key": DOC_KEY},
        {"Ref_Key": "f2", "ВладелецФайла_Key": DOC_KEY},
    ]
    deleted = delete_attached_files_for_document(client, document_ref_key=DOC_KEY)
    assert deleted == ["f1", "f2"]
    assert client.delete_entity.call_count == 2


def test_verify_attached_file_reference_fields_rejects_edit_lock():
    with pytest.raises(AttachedFileError, match="Редактирует_Key"):
        verify_attached_file_reference_fields(
            {
                "Ref_Key": DOC_KEY,
                "Редактирует_Key": AUTHOR_KEY,
                "ТипХраненияФайла": "ВИнформационнойБазе",
                "Том_Key": "00000000-0000-0000-0000-000000000000",
                "DeletionMark": False,
            },
            ref_key=DOC_KEY,
        )


def test_release_attached_file_edit_lock_verifies_cleared_lock():
    client = MagicMock()
    client.get_by_key.return_value = {
        "Ref_Key": DOC_KEY,
        "Редактирует_Key": "00000000-0000-0000-0000-000000000000",
    }
    release_attached_file_edit_lock(
        client,
        entity="Catalog_ТД_ВходящаяКорреспонденцияПрисоединенныеФайлы",
        ref_key=DOC_KEY,
        author_key=AUTHOR_KEY,
    )
    client.patch_entity.assert_called_once()
    payload = client.patch_entity.call_args[0][2]
    assert payload["Автор_Key"] == AUTHOR_KEY
    assert payload["Редактирует_Key"] == "00000000-0000-0000-0000-000000000000"
    assert "Изменил_Key" not in payload


def test_release_attached_file_edit_lock_raises_if_still_locked():
    client = MagicMock()
    client.get_by_key.return_value = {
        "Ref_Key": DOC_KEY,
        "Редактирует_Key": AUTHOR_KEY,
    }
    with pytest.raises(AttachedFileError, match="Блокировка"):
        release_attached_file_edit_lock(
            client,
            entity="Catalog_ТД_ВходящаяКорреспонденцияПрисоединенныеФайлы",
            ref_key=DOC_KEY,
        )
