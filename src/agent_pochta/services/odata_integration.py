"""OData-адаптер Integration Service → Document_ТД_ВходящаяКорреспонденция (1С)."""

from __future__ import annotations

import json
from typing import Any

from agent_pochta.schemas import EmailMessage, RoutingResult
from agent_pochta.services.integration_service import IntegrationService
from agent_pochta.services.odata_client import ODataClient
from agent_pochta.services.odata_incoming_mapper import (
    build_department_name_lookup,
    build_incoming_document_payload,
    load_field_map,
    resolve_guid_map,
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
        organization_keys_json: str = "",
        department_keys_json: str = "",
        organization_keys_file: str = "",
        department_keys_file: str = "",
        routing_rules_path: str = "",
    ) -> None:
        self._entity = entity.strip("/")
        self._field_map = load_field_map(field_map_json)
        parsed_extra = self._parse_extra_fields(extra_fields_json)
        self._extra_fields = parsed_extra if parsed_extra else {"Posted": False}
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
        self._client = ODataClient(
            base_url,
            username=username,
            password=password,
            timeout_sec=timeout_sec,
        )

    @staticmethod
    def _parse_extra_fields(raw: str) -> dict[str, Any]:
        if not raw.strip():
            return {}
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("ODATA_INCOMING_EXTRA_FIELDS must be a JSON object")
        return data

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
            extra_fields=self._extra_fields,
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
