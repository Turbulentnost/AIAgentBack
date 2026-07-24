"""OData-адаптер Integration Service → Document_ТД_ВходящаяКорреспонденция (1С)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agent_pochta.config import PROJECT_ROOT
from agent_pochta.schemas import EmailMessage, RoutingResult
from agent_pochta.services.integration_service import IntegrationService
from agent_pochta.services.odata_client import ODataClient
from agent_pochta.services.odata_incoming_mapper import (
    build_department_name_lookup,
    build_incoming_document_payload,
    build_incoming_document_update_payload,
    load_field_map,
    resolve_guid_map,
    resolve_incoming_extra_fields,
)
from agent_pochta.services.odata_attached_file import (
    AttachedFileError,
    AttachedFileInput,
    attach_files_to_incoming_document,
    delete_attached_files_for_document,
    load_attached_file_field_map,
    now_attached_file_processed_at,
)
from agent_pochta.services.routing_departments import load_routing_rules

_EMPTY_GUID = "00000000-0000-0000-0000-000000000000"


def resolve_attached_file_author_key(
    *,
    explicit_key: str = "",
    incoming_defaults_file: str | Path | None = None,
) -> str:
    """GUID пользователя 1С для Автор_Key присоединённого файла."""
    key = (explicit_key or "").strip()
    if key and key != _EMPTY_GUID:
        return key
    defaults_path = Path(incoming_defaults_file) if incoming_defaults_file else (
        PROJECT_ROOT / "data" / "odata_incoming_defaults.json"
    )
    if not defaults_path.is_absolute():
        defaults_path = PROJECT_ROOT / defaults_path
    if defaults_path.is_file():
        try:
            defaults = json.loads(defaults_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            defaults = {}
        if isinstance(defaults, dict):
            for field_name in ("Пользователь_Key", "Ответственный_Key"):
                fallback = (defaults.get(field_name) or "").strip()
                if fallback and fallback != _EMPTY_GUID:
                    return fallback
    return ""


class ODataIntegrationService(IntegrationService):
    """Создаёт документ «Входящая корреспонденция» через OData POST."""

    def __init__(
        self,
        base_url: str,
        *,
        entity: str,
        username: str = "",
        password: str = "",
        timeout_sec: float = 60.0,
        field_map_json: str = "",
        extra_fields_json: str = "",
        incoming_defaults_file: str = "",
        organization_keys_json: str = "",
        department_keys_json: str = "",
        organization_keys_file: str = "",
        department_keys_file: str = "",
        routing_rules_path: str = "",
        attached_file_field_map_path: str = "",
        attach_files_enabled: bool = True,
        file_volume_key: str = "",
        file_author_key: str = "",
    ) -> None:
        self._entity = entity.strip("/")
        self._field_map = load_field_map(field_map_json)
        self._extra_fields = resolve_incoming_extra_fields(
            extra_fields_json,
            file_path=incoming_defaults_file,
        )
        self._organization_keys = resolve_guid_map(
            organization_keys_json,
            file_path=organization_keys_file,
            env_name="ODATA_ORGANIZATION_KEYS",
        )
        self._department_keys = resolve_guid_map(
            department_keys_json,
            file_path=department_keys_file,
            env_name="ODATA_DEPARTMENT_KEYS",
        )
        self._department_names = build_department_name_lookup(
            load_routing_rules(routing_rules_path or None),
        )
        self._attached_file_field_map = self._resolve_attached_file_field_map(
            attached_file_field_map_path or None,
            file_volume_key=file_volume_key,
        )
        self._attach_files_enabled = attach_files_enabled
        self._file_author_key = resolve_attached_file_author_key(
            explicit_key=file_author_key,
            incoming_defaults_file=incoming_defaults_file,
        )
        self._client = ODataClient(
            base_url,
            username=username,
            password=password,
            timeout_sec=timeout_sec,
        )

    @staticmethod
    def _resolve_attached_file_field_map(
        path: str | None,
        *,
        file_volume_key: str = "",
    ) -> dict[str, Any]:
        field_map = load_attached_file_field_map(path)
        volume_key = (file_volume_key or "").strip()
        if not volume_key:
            return field_map
        defaults = dict(field_map.get("defaults") or {})
        defaults["volume_key"] = volume_key
        return {**field_map, "defaults": defaults}

    def create_incoming_correspondence(
        self,
        email: EmailMessage,
        routing: RoutingResult,
        summary_ru: str,
        *,
        xml_document: str | None = None,
    ) -> dict:
        payload = build_incoming_document_payload(
            email,
            routing,
            summary_ru,
            xml_document=xml_document,
            field_map=self._field_map,
            extra_fields=self._extra_fields or None,
            organization_keys=self._organization_keys,
            department_keys=self._department_keys,
            department_names=self._department_names,
        )
        data = self._client.create_entity(self._entity, payload)
        ref_key = data.get("Ref_Key")
        number = data.get("Number")
        return {
            "erp_document_number": number,
            # Задачи в Документообороте создаются вручную; здесь только документ 1С.
            "erp_task_id": None,
            "erp_document_id": ref_key,
            "fields": payload,
            "odata_response": data,
        }

    def update_incoming_correspondence(
        self,
        document_ref_key: str,
        email: EmailMessage,
        routing: RoutingResult,
        summary_ru: str,
        *,
        xml_document: str | None = None,
    ) -> dict:
        """PATCH полей документа после коррекции оператора."""
        ref_key = (document_ref_key or "").strip()
        if not ref_key:
            raise ValueError("document_ref_key is required")
        payload = build_incoming_document_update_payload(
            email,
            routing,
            summary_ru,
            xml_document=xml_document,
            field_map=self._field_map,
            extra_fields=None,
            organization_keys=self._organization_keys,
            department_keys=self._department_keys,
            department_names=self._department_names,
        )
        if not payload:
            return {
                "updated": False,
                "erp_document_id": ref_key,
                "fields": {},
            }
        self._client.patch_entity(self._entity, ref_key, payload)
        return {
            "updated": True,
            "erp_document_id": ref_key,
            "fields": payload,
        }

    def attach_files_to_incoming_correspondence(
        self,
        *,
        document_ref_key: str,
        files: list[AttachedFileInput],
    ) -> list[dict]:
        if not self._attach_files_enabled:
            return []
        if not files:
            return []

        processed_at = now_attached_file_processed_at()
        author_key = self._file_author_key
        if not author_key or author_key == _EMPTY_GUID:
            raise AttachedFileError(
                "Автор_Key не задан: укажите ODATA_FILE_AUTHOR_KEY "
                "или Пользователь_Key / Ответственный_Key в odata_incoming_defaults.json"
            )

        enriched: list[AttachedFileInput] = []
        for item in files:
            author = item.author_key or author_key
            enriched.append(
                AttachedFileInput(
                    filename=item.filename,
                    content=item.content,
                    author_key=author,
                    edited_by_key=item.edited_by_key,
                    comment=item.comment,
                    processed_at=item.processed_at or processed_at,
                )
            )

        results = attach_files_to_incoming_document(
            self._client,
            document_ref_key=document_ref_key,
            files=enriched,
            field_map=self._attached_file_field_map,
        )
        return [
            {
                "ref_key": item.ref_key,
                "filename": (
                    f"{item.filename}.{item.extension}"
                    if item.extension
                    else item.filename
                ),
                "extension": item.extension,
                "size_bytes": item.size_bytes,
                "entity": item.entity,
            }
            for item in results
        ]

    def delete_attached_files_for_document(self, document_ref_key: str) -> list[str]:
        """DELETE всех присоединённых файлов документа (перед force reattach)."""
        if not self._attach_files_enabled:
            return []
        return delete_attached_files_for_document(
            self._client,
            document_ref_key=document_ref_key,
            field_map=self._attached_file_field_map,
        )
