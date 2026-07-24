"""Прикрепление файлов к Document_ТД_ВходящаяКорреспонденция через OData (ТЗ БСП)."""

from __future__ import annotations

import base64
import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from agent_pochta.config import PROJECT_ROOT

_DEFAULT_MAP_PATH = PROJECT_ROOT / "data" / "odata_attached_file_field_map.json"
_EMPTY_GUID = "00000000-0000-0000-0000-000000000000"
_MSK = ZoneInfo("Europe/Moscow")
_VOLUME_STORAGE_KIND = "ВТомахНаДиске"
_DATABASE_STORAGE_KIND = "ВИнформационнойБазе"
_DEFAULT_VOLUME_KEY = "21886495-364e-11ea-82f2-ac1f6b05524c"
_VOLUME_BINARY_TYPE = "application/xml+xdto"
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
                "file_path": "ПутьКФайлу",
                "storage_binary": "ФайлХранилище_Base64Data",
                "storage_binary_type": "ФайлХранилище_Type",
                "storage_stream": "ФайлХранилище",
                "storage_kind": "ТипХраненияФайла",
                "size": "Размер",
                "created_at": "ДатаСоздания",
                "modified_at": "ДатаМодификацииУниверсальная",
                "author_key": "Автор_Key",
                "edit_lock_key": "Редактирует_Key",
            },
            "defaults": {
                "storage_mode": "database",
                "storage_kind": _DATABASE_STORAGE_KIND,
                "volume_key": _DEFAULT_VOLUME_KEY,
                "volume_binary_type": _VOLUME_BINARY_TYPE,
                "storage_binary_type": "application/octet-stream",
                "text_storage_type": _VOLUME_BINARY_TYPE,
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
                "include_static_fields": False,
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
    storage_mode: str | None = None,
) -> str:
    """Content-Type для PUT в Edm.Stream.

    На томе 1С ожидает application/xml+xdto (как ФайлХранилище_Type у ручных загрузок).
    В ИБ — message/rfc822 / application/vnd.ms-outlook / octet-stream по расширению.
    """
    cfg_defaults = defaults or {}
    mode = storage_mode or resolve_attached_file_storage_mode(cfg_defaults)
    if mode == "volume":
        return str(cfg_defaults.get("volume_binary_type") or _VOLUME_BINARY_TYPE)
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


def _field_name(fields: dict[str, Any], key: str, fallback: str = "") -> str:
    return str(fields.get(key) or fallback).strip()


def _apply_static_attached_file_fields(
    payload: dict[str, Any],
    *,
    fields: dict[str, Any],
    defaults: dict[str, Any],
) -> None:
    """Заполняет булевы/строковые поля по шаблону рабочей записи АЛ00-000760."""
    static_bool = (
        ("deletion_mark", "DeletionMark", "deletion_mark"),
        ("is_folder", "IsFolder", "is_folder"),
        ("store_versions", "ХранитьВерсии", "store_versions"),
        ("signed_ep", "ПодписанЭП", "signed_ep"),
        ("encrypted", "Зашифрован", "encrypted"),
    )
    for map_key, fallback_name, default_key in static_bool:
        if name := _field_name(fields, map_key, fallback_name):
            payload[name] = bool(defaults.get(default_key, False))

    static_values: tuple[tuple[str, str, Any], ...] = (
        ("parent_key", "Parent_Key", _EMPTY_GUID),
        ("comment", "Описание", defaults.get("comment", "")),
        ("image_index", "ИндексКартинки", defaults.get("image_index", "0")),
        ("loan_date", "ДатаЗаема", defaults.get("loan_date", "0001-01-01T00:00:00")),
        (
            "text_extraction_status",
            "СтатусИзвлеченияТекста",
            defaults.get("text_extraction_status", ""),
        ),
        (
            "text_storage_type",
            "ТекстХранилище_Type",
            defaults.get("text_storage_type", _VOLUME_BINARY_TYPE),
        ),
        (
            "text_storage_binary",
            "ТекстХранилище_Base64Data",
            defaults.get("text_storage_binary", ""),
        ),
    )
    for map_key, fallback_name, value in static_values:
        if name := _field_name(fields, map_key, fallback_name):
            payload[name] = value


def resolve_attached_file_storage_mode(defaults: dict[str, Any] | None = None) -> str:
    """Режим хранения: volume (том на диске) или database (ИБ + Base64/stream)."""
    cfg = defaults or {}
    mode = str(cfg.get("storage_mode") or "").strip().casefold()
    if mode in {"volume", "tom", "disk", "volumes", "втomахнадиске", "втомахнадиске"}:
        return "volume"
    if mode in {"database", "db", "ib", "info_base", "информационнойбазе"}:
        return "database"
    kind = str(cfg.get("storage_kind") or "").strip()
    if kind == _VOLUME_STORAGE_KIND:
        return "volume"
    if kind == _DATABASE_STORAGE_KIND:
        return "database"
    return "database"


def is_volume_storage_kind(storage_kind: str | None) -> bool:
    return str(storage_kind or "").strip() == _VOLUME_STORAGE_KIND


def build_volume_storage_filename(base_name: str, extension: str = "") -> str:
    """Имя файла на томе: Description + .Расширение (как в ручных загрузках 1С)."""
    base = str(base_name or "").strip()
    ext = str(extension or "").strip().lstrip(".")
    if ext:
        if not base:
            raise AttachedFileError("Имя файла на томе не задано")
        return f"{base}.{ext}"
    if not base:
        raise AttachedFileError("Имя файла на томе не задано")
    return base


def format_volume_file_path(
    processed_at: datetime,
    storage_filename: str,
) -> str:
    """Относительный путь на томе: YYYYMMDD\\ИМЯ.расш (MSK, как в ручных загрузках 1С)."""
    ts = _coerce_processing_timestamp(processed_at).astimezone(_MSK)
    date_part = ts.strftime("%Y%m%d")
    filename = str(storage_filename or "").strip().replace("/", "\\")
    if not filename or "\\" in filename:
        raise AttachedFileError(f"Некорректное имя файла на томе: {storage_filename!r}")
    return f"{date_part}\\{filename}"


def resolve_attached_file_upload_plan(
    defaults: dict[str, Any] | None = None,
    *,
    include_binary: bool | None = None,
) -> dict[str, Any]:
    """План POST/PUT для режима volume vs database."""
    cfg = defaults or {}
    mode = resolve_attached_file_storage_mode(cfg)
    if mode == "volume":
        upload_via_stream = True
        storage_kind = _VOLUME_STORAGE_KIND
        if include_binary is None:
            include_binary = False
        binary_type = str(cfg.get("volume_binary_type") or _VOLUME_BINARY_TYPE)
    else:
        upload_via_stream = bool(cfg.get("upload_binary_via_stream", False))
        storage_kind = _DATABASE_STORAGE_KIND if upload_via_stream else str(
            cfg.get("storage_kind") or _DATABASE_STORAGE_KIND
        )
        if include_binary is None:
            include_binary = not upload_via_stream
        binary_type = str(cfg.get("storage_binary_type") or "application/octet-stream")
    return {
        "mode": mode,
        "storage_kind": storage_kind,
        "include_binary": include_binary,
        "upload_via_stream": upload_via_stream,
        "binary_type": binary_type,
    }


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
    processed_at = _coerce_processing_timestamp(file_input.processed_at)
    plan = resolve_attached_file_upload_plan(defaults, include_binary=include_binary)
    storage_kind = plan["storage_kind"]
    if include_binary is None:
        include_binary = plan["include_binary"]
    else:
        include_binary = bool(include_binary)

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
    if plan["mode"] == "volume":
        volume_value = (defaults.get("volume_key") or _DEFAULT_VOLUME_KEY).strip()
        if volume_field := fields.get("volume_key"):
            if volume_value and volume_value != _EMPTY_GUID:
                payload[str(volume_field)] = volume_value
        if path_field := fields.get("file_path"):
            payload[str(path_field)] = format_volume_file_path(
                processed_at,
                build_volume_storage_filename(base_name, extension),
            )
        if storage_type_field := fields.get("storage_binary_type"):
            payload[str(storage_type_field)] = plan["binary_type"]
    else:
        # Database mode: omit Том_Key/ПутьКФайлу (1С defaults; шаблон АЛ00-000760 / commit 406461f).
        if volume_value := (defaults.get("volume_key") or "").strip():
            if (
                volume_value
                and volume_value != _EMPTY_GUID
                and storage_kind == _VOLUME_STORAGE_KIND
            ):
                if volume_field := fields.get("volume_key"):
                    payload[str(volume_field)] = volume_value
    if include_binary:
        if storage_field := fields.get("storage_binary"):
            payload[str(storage_field)] = base64.b64encode(content).decode("ascii")
        if storage_type_field := fields.get("storage_binary_type"):
            if plan["mode"] != "volume":
                # Base64 POST: 1С падает с 500 на application/vnd.ms-outlook / message/rfc822.
                payload[str(storage_type_field)] = plan["binary_type"]
    if kind_field := fields.get("storage_kind"):
        payload[str(kind_field)] = storage_kind
    if size_field := fields.get("size"):
        payload[str(size_field)] = len(content)
    if created_field := fields.get("created_at"):
        payload[str(created_field)] = format_attached_file_created_at(processed_at)
    if modified_field := fields.get("modified_at"):
        payload[str(modified_field)] = format_attached_file_modified_universal(processed_at)
    if file_input.author_key and (author_field := fields.get("author_key")):
        payload[str(author_field)] = file_input.author_key
    # Изменил_Key — только при явном edited_by_key (шаблон АЛ00-000760: пустой GUID).
    modified_by = (file_input.edited_by_key or "").strip()
    if modified_by and modified_by != _EMPTY_GUID and (
        modified_field := fields.get("modified_by_key")
    ):
        payload[str(modified_field)] = modified_by
    # Редактирует_Key — блокировка «файл занят» в БСП; не заполняем на POST.
    if file_input.comment and (comment_field := fields.get("comment")):
        payload[str(comment_field)] = file_input.comment.strip()

    if defaults.get("include_static_fields"):
        _apply_static_attached_file_fields(payload, fields=fields, defaults=defaults)

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


def verify_attached_file_reference_fields(
    record: dict[str, Any],
    *,
    ref_key: str | None = None,
) -> None:
    """Проверяет ссылочные поля после POST/PATCH (1С падает при открытии иначе)."""
    lock_field = "Редактирует_Key"
    lock_value = str(record.get(lock_field) or "").strip()
    if lock_value and lock_value != _EMPTY_GUID:
        label = ref_key or record.get("Ref_Key") or "?"
        raise AttachedFileError(
            f"Блокировка {lock_field} не снята (Ref_Key={label}, значение={lock_value!r})"
        )

    storage_kind = str(record.get("ТипХраненияФайла") or "").strip()
    volume_key = str(record.get("Том_Key") or "").strip()
    file_path = str(record.get("ПутьКФайлу") or "").strip()
    if storage_kind == _DATABASE_STORAGE_KIND and volume_key and volume_key != _EMPTY_GUID:
        label = ref_key or record.get("Ref_Key") or "?"
        raise AttachedFileError(
            f"Том_Key заполнен при хранении в ИБ (Ref_Key={label}, Том_Key={volume_key!r})"
        )
    if is_volume_storage_kind(storage_kind):
        label = ref_key or record.get("Ref_Key") or "?"
        if not volume_key or volume_key == _EMPTY_GUID:
            raise AttachedFileError(
                f"Том_Key не задан при хранении в томе (Ref_Key={label})"
            )
        if not file_path:
            raise AttachedFileError(
                f"ПутьКФайлу пуст при хранении в томе (Ref_Key={label})"
            )

    if record.get("DeletionMark") is True:
        label = ref_key or record.get("Ref_Key") or "?"
        raise AttachedFileError(f"Присоединённый файл помечен на удаление (Ref_Key={label})")


def verify_attached_file_storage(
    client,
    *,
    entity: str,
    ref_key: str,
    expected_size: int,
    field_map: dict[str, Any] | None = None,
) -> int:
    """Проверяет, что после POST в хранилище записаны ненулевые байты или метаданные тома."""
    cfg = field_map or load_attached_file_field_map()
    fields = cfg.get("fields") or {}
    record = client.get_by_key(entity, ref_key)
    if not record:
        raise AttachedFileError(
            f"OData GET {entity} с Ref_Key={ref_key} не вернул запись после POST"
        )

    kind_field = str(fields.get("storage_kind") or "ТипХраненияФайла")
    size_field = str(fields.get("size") or "Размер")
    path_field = str(fields.get("file_path") or "ПутьКФайлу")
    storage_kind = str(record.get(kind_field) or "").strip()
    meta_size_raw = record.get(size_field)
    try:
        meta_size = int(meta_size_raw) if meta_size_raw is not None else 0
    except (TypeError, ValueError):
        meta_size = 0

    if is_volume_storage_kind(storage_kind):
        file_path = str(record.get(path_field) or "").strip()
        volume_key = str(record.get(fields.get("volume_key") or "Том_Key") or "").strip()
        if not file_path:
            raise AttachedFileError(
                f"ПутьКФайлу пуст после POST (Ref_Key={ref_key}, ТипХранения={storage_kind!r})"
            )
        if not volume_key or volume_key == _EMPTY_GUID:
            raise AttachedFileError(
                f"Том_Key не задан после POST (Ref_Key={ref_key}, ТипХранения={storage_kind!r})"
            )
        if expected_size > 0 and meta_size != expected_size:
            raise AttachedFileError(
                f"Размер в метаданных ({meta_size}) не совпадает с отправленным ({expected_size})"
            )
        stored_bytes = read_attached_file_storage_bytes(
            client,
            entity=entity,
            ref_key=ref_key,
            field_map=cfg,
        )
        stored_size = len(stored_bytes)
        # На томе stream GET часто 0 байт — это норма для ручных загрузок 1С.
        if stored_size > 0:
            if expected_size > 0 and stored_size != expected_size:
                raise AttachedFileError(
                    f"Размер в stream ({stored_size}) не совпадает с отправленным ({expected_size})"
                )
            if meta_size > 0 and meta_size != stored_size:
                raise AttachedFileError(
                    f"Размер в метаданных ({meta_size}) не совпадает с хранилищем ({stored_size})"
                )
            return stored_size
        return meta_size or expected_size

    stored_bytes = read_attached_file_storage_bytes(
        client,
        entity=entity,
        ref_key=ref_key,
        field_map=cfg,
    )
    stored_size = len(stored_bytes)
    if stored_size == 0:
        raise AttachedFileError(
            "Пустое хранилище файла после POST "
            f"(Ref_Key={ref_key}, ТипХранения={storage_kind!r}, Размер={meta_size})"
        )

    if expected_size > 0 and stored_size != expected_size:
        raise AttachedFileError(
            f"Размер в хранилище ({stored_size}) не совпадает с отправленным ({expected_size})"
        )

    if meta_size > 0 and meta_size != stored_size:
        raise AttachedFileError(
            f"Размер в метаданных ({meta_size}) не совпадает с хранилищем ({stored_size})"
        )

    return stored_size


def list_attached_files_for_document(
    client,
    *,
    document_ref_key: str,
    field_map: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Все присоединённые файлы документа (включая помеченные на удаление)."""
    cfg = field_map or load_attached_file_field_map()
    entity = str(cfg.get("entity") or "Catalog_ТД_ВходящаяКорреспонденцияПрисоединенныеФайлы").strip()
    owner_key = normalize_document_ref_key(document_ref_key)
    owner_field = str((cfg.get("fields") or {}).get("owner_key") or "ВладелецФайла_Key")
    filter_expr = f"{owner_field} eq guid'{owner_key}'"
    fetch_filtered = getattr(client, "fetch_filtered", None)
    if callable(fetch_filtered):
        return fetch_filtered(entity, filter_expr=filter_expr)
    fetch_all = getattr(client, "fetch_all", None)
    if not callable(fetch_all):
        raise AttachedFileError("OData client does not support listing attached files")
    rows = fetch_all(entity)
    return [row for row in rows if str(row.get(owner_field) or "").strip().casefold() == owner_key.casefold()]


def delete_attached_files_for_document(
    client,
    *,
    document_ref_key: str,
    field_map: dict[str, Any] | None = None,
) -> list[str]:
    """DELETE всех присоединённых файлов документа. Возвращает удалённые Ref_Key."""
    cfg = field_map or load_attached_file_field_map()
    entity = str(cfg.get("entity") or "Catalog_ТД_ВходящаяКорреспонденцияПрисоединенныеФайлы").strip()
    delete_entity = getattr(client, "delete_entity", None)
    if not callable(delete_entity):
        raise AttachedFileError("OData client does not support DELETE")
    deleted: list[str] = []
    for item in list_attached_files_for_document(
        client,
        document_ref_key=document_ref_key,
        field_map=cfg,
    ):
        ref_key = str(item.get("Ref_Key") or "").strip()
        if not ref_key:
            continue
        try:
            delete_entity(entity, ref_key)
        except Exception as exc:
            raise AttachedFileError(
                f"Не удалось удалить присоединённый файл Ref_Key={ref_key}: {exc}"
            ) from exc
        deleted.append(ref_key)
    return deleted


def patch_attached_file_metadata(
    client,
    *,
    entity: str,
    ref_key: str,
    payload: dict[str, Any],
    field_map: dict[str, Any] | None = None,
) -> None:
    """PATCH метаданных файла с проверкой записи (If-Match: * в клиенте)."""
    if not payload:
        return
    patch_entity = getattr(client, "patch_entity", None)
    if not callable(patch_entity):
        return
    try:
        patch_entity(entity, ref_key, payload)
    except Exception as exc:
        fields = ", ".join(sorted(payload))
        raise AttachedFileError(
            f"Не удалось обновить поля ({fields}) Ref_Key={ref_key}: {exc}"
        ) from exc


def release_attached_file_edit_lock(
    client,
    *,
    entity: str,
    ref_key: str,
    field_map: dict[str, Any] | None = None,
    author_key: str | None = None,
) -> None:
    """Снимает блокировку Редактирует_Key после OData POST (иначе файл «недоступен» в 1С).

    Редактирует_Key — флаг «файл занят», не «кто редактировал»; целевое значение — пустой GUID.
    Изменил_Key не трогаем — рабочий шаблон АЛ00-000760 оставляет его пустым.
    """
    cfg = field_map or load_attached_file_field_map()
    fields = cfg.get("fields") or {}
    lock_field = str(fields.get("edit_lock_key") or "Редактирует_Key").strip()
    patch_payload: dict[str, Any] = {}
    if lock_field:
        patch_payload[lock_field] = _EMPTY_GUID
    if not patch_payload:
        return
    patch_attached_file_metadata(
        client,
        entity=entity,
        ref_key=ref_key,
        payload=patch_payload,
        field_map=cfg,
    )
    if not lock_field:
        return
    record = client.get_by_key(entity, ref_key) or {}
    current_lock = str(record.get(lock_field) or "").strip()
    if current_lock and current_lock != _EMPTY_GUID:
        raise AttachedFileError(
            f"Блокировка {lock_field} не снята после PATCH "
            f"(Ref_Key={ref_key}, значение={current_lock!r})"
        )


def upload_attached_file_binary(
    client,
    *,
    entity: str,
    ref_key: str,
    content: bytes,
    field_map: dict[str, Any] | None = None,
    filename: str | None = None,
    content_type: str | None = None,
    retries: int = 1,
    retry_delay_sec: float = 0.5,
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
    storage_mode = resolve_attached_file_storage_mode(defaults)
    primary_type = (
        content_type
        or (
            resolve_stream_content_type(
                filename,
                defaults=defaults,
                storage_mode=storage_mode,
            )
            if filename
            else None
        )
        or (
            defaults.get("volume_binary_type")
            if storage_mode == "volume"
            else defaults.get("storage_binary_type")
        )
        or "application/octet-stream"
    )
    fallback_types: list[str] = []
    for candidate in (
        primary_type,
        "application/octet-stream",
        str(defaults.get("volume_binary_type") or _VOLUME_BINARY_TYPE),
    ):
        value = str(candidate or "").strip()
        if value and value not in fallback_types:
            fallback_types.append(value)

    last_exc: Exception | None = None
    for content_type_value in fallback_types:
        for attempt in range(max(retries, 0) + 1):
            try:
                put_stream(
                    entity,
                    ref_key,
                    stream_property,
                    binary,
                    content_type=content_type_value,
                )
                return
            except Exception as exc:
                last_exc = exc
                if attempt >= retries:
                    break
                time.sleep(retry_delay_sec)
    raise AttachedFileError(
        f"Не удалось записать stream {stream_property} Ref_Key={ref_key}: {last_exc}"
    ) from last_exc


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
    defaults = cfg.get("defaults") or {}
    plan = resolve_attached_file_upload_plan(defaults)
    data = client.create_entity(entity, payload)
    ref_key = str(data.get("Ref_Key") or "").strip()
    if not ref_key:
        raise AttachedFileError(
            f"OData создал запись {entity}, но Ref_Key отсутствует в ответе"
        )

    if plan["mode"] == "volume":
        path_field = str(fields.get("file_path") or "ПутьКФайлу")
        expected_path = str(payload.get(path_field) or "").strip()
        if expected_path:
            record = client.get_by_key(entity, ref_key) or {}
            current_path = str(record.get(path_field) or "").strip()
            if not current_path:
                # PATCH ПутьКФайлу через OData не поддерживается 1С — только POST.
                raise AttachedFileError(
                    f"OData POST не заполнил {path_field} (Ref_Key={ref_key}); "
                    f"ожидался путь {expected_path!r}"
                )

    author_key = file_input.author_key

    if plan["upload_via_stream"]:
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

    release_attached_file_edit_lock(
        client,
        entity=entity,
        ref_key=ref_key,
        field_map=cfg,
        author_key=author_key,
    )

    final_record = client.get_by_key(entity, ref_key) or {}
    verify_attached_file_reference_fields(final_record, ref_key=ref_key)

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
