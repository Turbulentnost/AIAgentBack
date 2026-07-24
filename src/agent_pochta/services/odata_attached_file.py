"""Прикрепление файлов к Document_ТД_ВходящаяКорреспонденция через OData (ТЗ БСП)."""

from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from agent_pochta.config import PROJECT_ROOT

_DEFAULT_MAP_PATH = PROJECT_ROOT / "data" / "odata_attached_file_field_map.json"
_EMPTY_GUID = "00000000-0000-0000-0000-000000000000"
_MSK = ZoneInfo("Europe/Moscow")
_GUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


class AttachedFileError(ValueError):
    """Ошибка валидации или записи присоединённого файла."""


@dataclass(frozen=True)
class AttachedFileInput:
    """Вход: имя + двоичные данные (или путь задаётся снаружи)."""

    filename: str
    content: bytes
    author_key: str | None = None
    edited_by_key: str | None = None
    comment: str | None = None
    processed_at: datetime | None = None


@dataclass(frozen=True)
class AttachedFileResult:
    """Результат успешного создания элемента справочника файлов."""

    ref_key: str
    filename: str
    extension: str
    size_bytes: int
    entity: str
    odata_response: dict[str, Any]


def load_attached_file_field_map(path: str | Path | None = None) -> dict[str, Any]:
    file_path = Path(path) if path else _DEFAULT_MAP_PATH
    if not file_path.is_file():
        return {
            "entity": "Catalog_ТД_ВходящаяКорреспонденцияПрисоединенныеФайлы",
            "owner_document_entity": "Document_ТД_ВходящаяКорреспонденция",
            "fields": {
                "name": "Description",
                "extension": "Расширение",
                # 1С OData: ссылка на документ — *_Key, не ВладелецФайла.
                "owner_key": "ВладелецФайла_Key",
                "volume_key": "Том_Key",
                "storage_binary": "ФайлХранилище_Base64Data",
                "storage_binary_type": "ФайлХранилище_Type",
                "storage_stream": "ФайлХранилище",
                "storage_kind": "ТипХраненияФайла",
                "size": "Размер",
                "created_at": "ДатаСоздания",
                "modified_at": "ДатаМодификацииУниверсальная",
                "author_key": "Автор_Key",
                "edited_by_key": "Редактирует_Key",
            },
            "defaults": {
                # Двоичное содержимое вложения (не XDTO-обёртка пустого хранилища).
                "storage_binary_type": "application/octet-stream",
                # Base64 в POST надёжнее PUT Edm.Stream (PUT даёт 200, но 0 байт в ИБ).
                "storage_kind": "ВИнформационнойБазе",
                "upload_binary_via_stream": False,
            },
        }
    data = json.loads(file_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("odata_attached_file_field_map must be a JSON object")
    return data


def split_filename(filename: str) -> tuple[str, str]:
    """«Документ.pdf» → («Документ», «pdf»)."""
    name = (filename or "").strip().replace("\\", "/").rsplit("/", 1)[-1]
    if not name or name in {".", ".."}:
        raise AttachedFileError("Имя файла не задано")
    if "." in name:
        base, ext = name.rsplit(".", 1)
        base = base.strip()
        ext = ext.strip().lower()
        if not base:
            raise AttachedFileError(f"Некорректное имя файла: {filename!r}")
        if not ext:
            raise AttachedFileError(f"Не удалось определить расширение: {filename!r}")
        return base, ext
    return name, ""


def normalize_document_ref_key(document_ref_key: str) -> str:
    """Проверяет GUID документа-владельца."""
    value = (document_ref_key or "").strip()
    if not value or value == _EMPTY_GUID:
        raise AttachedFileError("Ссылка на документ ТД_ВходящаяКорреспонденция не заполнена")
    if not _GUID_RE.match(value):
        raise AttachedFileError(f"Некорректный Ref_Key документа: {value!r}")
    return value


def validate_file_content(content: bytes | None) -> bytes:
    if content is None or len(content) == 0:
        raise AttachedFileError("Пустой файл: двоичные данные отсутствуют")
    return content


def resolve_stream_content_type(
    filename: str,
    *,
    defaults: dict[str, Any] | None = None,
) -> str:
    """Content-Type для PUT в Edm.Stream (Outlook/1С открывают .eml как message/rfc822)."""
    cfg_defaults = defaults or {}
    try:
        _, extension = split_filename(filename)
    except AttachedFileError:
        extension = ""
    if extension == "eml":
        return "message/rfc822"
    if extension == "msg":
        return "application/vnd.ms-outlook"
    return str(cfg_defaults.get("storage_binary_type") or "application/octet-stream")


def now_attached_file_processed_at() -> datetime:
    """Момент прикрепления файла в MSK (для ДатаСоздания в 1С)."""
    return datetime.now(_MSK)


def _coerce_processing_timestamp(value: datetime | None) -> datetime:
    if value is None:
        return now_attached_file_processed_at()
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def format_attached_file_created_at(value: datetime) -> str:
    """ДатаСоздания: локальное MSK без tz (как в рабочих записях 1С)."""
    ts = _coerce_processing_timestamp(value).astimezone(_MSK)
    return ts.replace(microsecond=0, tzinfo=None).strftime("%Y-%m-%dT%H:%M:%S")


def format_attached_file_modified_universal(value: datetime) -> str:
    """ДатаМодификацииУниверсальная: UTC без tz."""
    ts = _coerce_processing_timestamp(value).astimezone(timezone.utc)
    return ts.replace(microsecond=0, tzinfo=None).strftime("%Y-%m-%dT%H:%M:%S")


def build_attached_file_payload(
    *,
    document_ref_key: str,
    file_input: AttachedFileInput,
    field_map: dict[str, Any] | None = None,
    include_binary: bool | None = None,
) -> tuple[str, dict[str, Any]]:
    """Формирует OData POST payload для справочника присоединённых файлов."""
    cfg = field_map or load_attached_file_field_map()
    entity = str(cfg.get("entity") or "Catalog_ТД_ВходящаяКорреспонденцияПрисоединенныеФайлы").strip()
    fields = cfg.get("fields") or {}
    defaults = cfg.get("defaults") or {}

    owner_key = normalize_document_ref_key(document_ref_key)
    content = validate_file_content(file_input.content)
    base_name, extension = split_filename(file_input.filename)
    upload_via_stream = bool(defaults.get("upload_binary_via_stream", False))
    if include_binary is None:
        include_binary = not upload_via_stream
    if upload_via_stream:
        storage_kind = "ВИнформационнойБазе"
    else:
        storage_kind = str(defaults.get("storage_kind") or "ВТомахНаДиске")

    payload: dict[str, Any] = {}

    if name_field := fields.get("name"):
        payload[str(name_field)] = base_name
    if ext_field := fields.get("extension"):
        payload[str(ext_field)] = extension
    owner_field_name = str(fields.get("owner_key") or "")
    if owner_field_name:
        payload[owner_field_name] = owner_key
    # При *_Key поле типа владельца не передаём (формат OData 1С).
    if owner_type_field := fields.get("owner_type"):
        owner_type_value = defaults.get("owner_type")
        if owner_type_value and not owner_field_name.endswith("_Key"):
            payload[str(owner_type_field)] = owner_type_value
    if volume_field := fields.get("volume_key"):
        volume_value = (defaults.get("volume_key") or "").strip()
        if (
            volume_value
            and volume_value != _EMPTY_GUID
            and storage_kind == "ВТомахНаДиске"
        ):
            payload[str(volume_field)] = volume_value
    if include_binary:
        if storage_field := fields.get("storage_binary"):
            payload[str(storage_field)] = base64.b64encode(content).decode("ascii")
        if storage_type_field := fields.get("storage_binary_type"):
            # Base64 POST: 1С падает с 500 на application/vnd.ms-outlook / message/rfc822.
            payload[str(storage_type_field)] = str(
                defaults.get("storage_binary_type") or "application/octet-stream"
            )
    if kind_field := fields.get("storage_kind"):
        payload[str(kind_field)] = storage_kind
    if size_field := fields.get("size"):
        payload[str(size_field)] = len(content)
    processed_at = _coerce_processing_timestamp(file_input.processed_at)
    if created_field := fields.get("created_at"):
        payload[str(created_field)] = format_attached_file_created_at(processed_at)
    if modified_field := fields.get("modified_at"):
        payload[str(modified_field)] = format_attached_file_modified_universal(processed_at)
    if file_input.author_key and (author_field := fields.get("author_key")):
        payload[str(author_field)] = file_input.author_key
    edited_by = file_input.edited_by_key or file_input.author_key
    if edited_by and (edited_by_field := fields.get("edited_by_key")):
        payload[str(edited_by_field)] = edited_by
    if file_input.comment and (comment_field := fields.get("comment")):
        payload[str(comment_field)] = file_input.comment.strip()

    if not payload:
        raise AttachedFileError("Маппинг полей присоединённого файла пуст — проверьте field_map")

    return entity, payload


def read_attached_file_storage_bytes(
    client,
    *,
    entity: str,
    ref_key: str,
    field_map: dict[str, Any] | None = None,
) -> bytes:
    """Читает фактические байты файла из OData (stream GET, затем Base64 в JSON)."""
    cfg = field_map or load_attached_file_field_map()
    fields = cfg.get("fields") or {}
    stream_property = str(fields.get("storage_stream") or "ФайлХранилище").strip()
    get_stream = getattr(client, "get_entity_stream", None)
    if callable(get_stream):
        content = get_stream(entity, ref_key, stream_property) or b""
        if content:
            return content

    record = client.get_by_key(entity, ref_key) or {}
    binary_field = str(fields.get("storage_binary") or "ФайлХранилище_Base64Data")
    b64 = record.get(binary_field) or ""
    if not b64:
        return b""
    try:
        return base64.b64decode(b64)
    except Exception as exc:
        raise AttachedFileError(f"Некорректный Base64 в {binary_field}: {exc}") from exc


def verify_attached_file_storage(
    client,
    *,
    entity: str,
    ref_key: str,
    expected_size: int,
    field_map: dict[str, Any] | None = None,
) -> int:
    """Проверяет, что после POST в хранилище записаны ненулевые байты."""
    cfg = field_map or load_attached_file_field_map()
    fields = cfg.get("fields") or {}
    record = client.get_by_key(entity, ref_key)
    if not record:
        raise AttachedFileError(
            f"OData GET {entity} с Ref_Key={ref_key} не вернул запись после POST"
        )

    stored_bytes = read_attached_file_storage_bytes(
        client,
        entity=entity,
        ref_key=ref_key,
        field_map=cfg,
    )
    stored_size = len(stored_bytes)
    if stored_size == 0:
        kind_field = str(fields.get("storage_kind") or "ТипХраненияФайла")
        size_field = str(fields.get("size") or "Размер")
        storage_kind = record.get(kind_field) or ""
        meta_size = record.get(size_field) or 0
        raise AttachedFileError(
            "Пустое хранилище файла после POST "
            f"(Ref_Key={ref_key}, ТипХранения={storage_kind!r}, Размер={meta_size})"
        )

    if expected_size > 0 and stored_size != expected_size:
        raise AttachedFileError(
            f"Размер в хранилище ({stored_size}) не совпадает с отправленным ({expected_size})"
        )

    size_field = str(fields.get("size") or "Размер")
    meta_size_raw = record.get(size_field)
    try:
        meta_size = int(meta_size_raw) if meta_size_raw is not None else 0
    except (TypeError, ValueError):
        meta_size = 0
    if meta_size > 0 and meta_size != stored_size:
        raise AttachedFileError(
            f"Размер в метаданных ({meta_size}) не совпадает с хранилищем ({stored_size})"
        )

    return stored_size


def upload_attached_file_binary(
    client,
    *,
    entity: str,
    ref_key: str,
    content: bytes,
    field_map: dict[str, Any] | None = None,
    filename: str | None = None,
    content_type: str | None = None,
) -> None:
    """PUT двоичных данных в Edm.Stream-свойство ФайлХранилище после POST метаданных."""
    cfg = field_map or load_attached_file_field_map()
    fields = cfg.get("fields") or {}
    defaults = cfg.get("defaults") or {}
    stream_property = str(fields.get("storage_stream") or "ФайлХранилище").strip()
    binary = validate_file_content(content)
    put_stream = getattr(client, "put_entity_stream", None)
    if not callable(put_stream):
        raise AttachedFileError("OData client does not support stream upload")
    put_stream(
        entity,
        ref_key,
        stream_property,
        binary,
        content_type=content_type
        or (
            resolve_stream_content_type(filename, defaults=defaults)
            if filename
            else None
        )
        or defaults.get("storage_binary_type")
        or "application/octet-stream",
    )


def attach_file_to_incoming_document(
    client,
    *,
    document_ref_key: str,
    file_input: AttachedFileInput,
    field_map: dict[str, Any] | None = None,
    verify_owner_exists: bool = True,
    owner_document_entity: str | None = None,
) -> AttachedFileResult:
    """Создаёт элемент справочника присоединённых файлов и привязывает к документу.

    Не создаёт и не изменяет документ-владелец.
    """
    cfg = field_map or load_attached_file_field_map()
    doc_entity = (
        owner_document_entity
        or cfg.get("owner_document_entity")
        or "Document_ТД_ВходящаяКорреспонденция"
    )
    owner_key = normalize_document_ref_key(document_ref_key)

    if verify_owner_exists:
        existing = client.get_by_key(str(doc_entity), owner_key)
        if not existing:
            raise AttachedFileError(
                f"Документ {doc_entity} с Ref_Key={owner_key} не найден — файл не создан"
            )

    entity, payload = build_attached_file_payload(
        document_ref_key=owner_key,
        file_input=file_input,
        field_map=cfg,
    )
    fields = cfg.get("fields") or {}
    data = client.create_entity(entity, payload)
    ref_key = str(data.get("Ref_Key") or "").strip()
    if not ref_key:
        raise AttachedFileError(
            f"OData создал запись {entity}, но Ref_Key отсутствует в ответе"
        )

    edited_by = file_input.edited_by_key or file_input.author_key
    if edited_by and (edited_by_field := fields.get("edited_by_key")):
        patch_payload = {str(edited_by_field): edited_by}
        patch_entity = getattr(client, "patch_entity", None)
        if callable(patch_entity):
            try:
                patch_entity(entity, ref_key, patch_payload)
            except Exception as exc:
                raise AttachedFileError(
                    f"Не удалось установить {edited_by_field} после POST: {exc}"
                ) from exc

    defaults = cfg.get("defaults") or {}
    if defaults.get("upload_binary_via_stream", False):
        upload_attached_file_binary(
            client,
            entity=entity,
            ref_key=ref_key,
            content=file_input.content,
            field_map=cfg,
            filename=file_input.filename,
        )

    verify_attached_file_storage(
        client,
        entity=entity,
        ref_key=ref_key,
        expected_size=len(file_input.content),
        field_map=cfg,
    )

    base_name, extension = split_filename(file_input.filename)
    return AttachedFileResult(
        ref_key=ref_key,
        filename=base_name,
        extension=extension,
        size_bytes=len(file_input.content),
        entity=entity,
        odata_response=data,
    )


def attach_files_to_incoming_document(
    client,
    *,
    document_ref_key: str,
    files: list[AttachedFileInput],
    field_map: dict[str, Any] | None = None,
    verify_owner_exists: bool = True,
) -> list[AttachedFileResult]:
    """Последовательно прикрепляет несколько файлов к одному документу."""
    results: list[AttachedFileResult] = []
    for item in files:
        results.append(
            attach_file_to_incoming_document(
                client,
                document_ref_key=document_ref_key,
                file_input=item,
                field_map=field_map,
                verify_owner_exists=verify_owner_exists and not results,
            )
        )
    return results
