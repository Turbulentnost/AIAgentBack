"""OData-адаптер Integration Service → Document_ТД_ВходящаяКорреспонденция (1С)."""

from __future__ import annotations

from typing import Any

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
    AttachedFileInput,
    attach_files_to_incoming_document,
    load_attached_file_field_map,
)
from agent_pochta.services.routing_departments import load_routing_rules


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
        self._attached_file_field_map = load_attached_file_field_map(
            attached_file_field_map_path or None
        )
        self._attach_files_enabled = attach_files_enabled
        self._client = ODataClient(
            base_url,
            username=username,
            password=password,
            timeout_sec=timeout_sec,
        )

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

        results = attach_files_to_incoming_document(
            self._client,
            document_ref_key=document_ref_key,
            files=files,
            field_map=self._attached_file_field_map,
        )
        return [
            {
                "ref_key": item.ref_key,
                "filename": item.filename,
                "extension": item.extension,
                "size_bytes": item.size_bytes,
                "entity": item.entity,
            }
            for item in results
        ]
