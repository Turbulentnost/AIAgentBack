"""Удаление связанных бизнес-процессов 1С при отметке письма как спам."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import structlog

from agent_pochta.config import get_settings
from agent_pochta.services.integration_service import IntegrationService
from agent_pochta.services.odata_client import ODataClient
from agent_pochta.services.odata_integration import ODataIntegrationService

logger = structlog.get_logger(__name__)

_EMPTY_GUID = "00000000-0000-0000-0000-000000000000"

DEFAULT_PROCESS_ENTITIES = (
    "BusinessProcess_Задание",
    "BusinessProcess_CRM_БизнесПроцесс",
)
FALLBACK_TASK_ENTITY = "Task_ЗадачаИсполнителя"


@dataclass(frozen=True)
class LinkedProcessRef:
    entity: str
    ref_key: str
    number: str | None = None
    deletion_mark: bool = False


def resolve_process_entities(raw: str = "") -> tuple[str, ...]:
    value = (raw or "").strip()
    if not value:
        settings = get_settings()
        value = (settings.odata_business_process_entities or "").strip()
    if not value:
        return DEFAULT_PROCESS_ENTITIES
    return tuple(item.strip() for item in value.split(",") if item.strip())


def incoming_document_subject_type(document_entity: str) -> str:
    entity = (document_entity or "Document_ТД_ВходящаяКорреспонденция").strip()
    if entity.startswith("StandardODATA."):
        return entity
    return f"StandardODATA.{entity}"


def _subject_type_matches(record_type: str | None, *, expected: str, document_entity: str) -> bool:
    value = (record_type or "").strip()
    if not value:
        return True
    if value == expected:
        return True
    if value == document_entity:
        return True
    return value.endswith(document_entity)


def find_linked_processes(
    client: ODataClient,
    document_ref_key: str,
    *,
    document_entity: str,
    process_entities: tuple[str, ...] | None = None,
    include_tasks_fallback: bool = True,
) -> list[LinkedProcessRef]:
    """Ищет BusinessProcess, где Предмет = Ref_Key входящей корреспонденции."""
    ref_key = (document_ref_key or "").strip()
    if not ref_key or ref_key == _EMPTY_GUID:
        return []

    entities = tuple(process_entities or resolve_process_entities())
    if include_tasks_fallback and FALLBACK_TASK_ENTITY not in entities:
        entities = (*entities, FALLBACK_TASK_ENTITY)

    expected_type = incoming_document_subject_type(document_entity)
    found: list[LinkedProcessRef] = []
    seen: set[tuple[str, str]] = set()
    filter_expr = f"Предмет eq '{ref_key}'"

    for entity in entities:
        try:
            rows = client.fetch_filtered(entity, filter_expr=filter_expr, page_size=100)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "erp_process_lookup_failed",
                entity=entity,
                document_ref_key=ref_key,
                error=str(exc),
            )
            continue

        for row in rows:
            if not isinstance(row, dict):
                continue
            if not _subject_type_matches(
                row.get("Предмет_Type"),
                expected=expected_type,
                document_entity=document_entity,
            ):
                continue
            process_ref = (row.get("Ref_Key") or "").strip()
            if not process_ref or process_ref == _EMPTY_GUID:
                continue
            key = (entity, process_ref)
            if key in seen:
                continue
            seen.add(key)
            found.append(
                LinkedProcessRef(
                    entity=entity,
                    ref_key=process_ref,
                    number=(row.get("Number") or None),
                    deletion_mark=bool(row.get("DeletionMark")),
                )
            )
    return found


def _delete_or_mark_process(client: ODataClient, item: LinkedProcessRef) -> dict[str, Any]:
    if item.deletion_mark:
        return {
            "entity": item.entity,
            "ref_key": item.ref_key,
            "number": item.number,
            "deleted": False,
            "skipped": "already_deleted",
        }

    try:
        client.delete_entity(item.entity, item.ref_key)
        return {
            "entity": item.entity,
            "ref_key": item.ref_key,
            "number": item.number,
            "deleted": True,
            "method": "delete",
        }
    except Exception as delete_exc:  # noqa: BLE001
        try:
            client.patch_entity(item.entity, item.ref_key, {"DeletionMark": True})
            return {
                "entity": item.entity,
                "ref_key": item.ref_key,
                "number": item.number,
                "deleted": True,
                "method": "deletion_mark",
                "delete_error": str(delete_exc),
            }
        except Exception as patch_exc:  # noqa: BLE001
            return {
                "entity": item.entity,
                "ref_key": item.ref_key,
                "number": item.number,
                "deleted": False,
                "error": str(patch_exc),
                "delete_error": str(delete_exc),
            }


def reset_incoming_document_process_flag(
    client: ODataClient,
    *,
    document_entity: str,
    document_ref_key: str,
) -> bool:
    ref_key = (document_ref_key or "").strip()
    if not ref_key:
        return False
    try:
        client.patch_entity(
            document_entity,
            ref_key,
            {"БизнесПроцессЗапущен": False},
        )
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "erp_process_reset_flag_failed",
            document_ref_key=ref_key,
            error=str(exc),
        )
        return False


def delete_linked_processes_for_incoming_document(
    integration: IntegrationService,
    *,
    document_ref_key: str,
    document_entity: str | None = None,
) -> dict[str, Any]:
    """Удаляет связанные процессы 1С для документа входящей корреспонденции."""
    ref_key = (document_ref_key or "").strip()
    if not ref_key:
        return {"ok": True, "skipped": True, "reason": "no_document_ref", "deleted": [], "found": []}

    if not isinstance(integration, ODataIntegrationService):
        return {"ok": True, "skipped": True, "reason": "integration_not_odata", "deleted": [], "found": []}

    settings = get_settings()
    doc_entity = (document_entity or settings.odata_incoming_doc_entity or "").strip()
    client = integration._client
    linked = find_linked_processes(
        client,
        ref_key,
        document_entity=doc_entity,
    )
    deleted = [_delete_or_mark_process(client, item) for item in linked]
    reset_flag = reset_incoming_document_process_flag(
        client,
        document_entity=doc_entity,
        document_ref_key=ref_key,
    )

    ok = all(item.get("deleted") or item.get("skipped") for item in deleted)
    return {
        "ok": ok,
        "skipped": False,
        "document_ref_key": ref_key,
        "found": [
            {
                "entity": item.entity,
                "ref_key": item.ref_key,
                "number": item.number,
                "deletion_mark": item.deletion_mark,
            }
            for item in linked
        ],
        "deleted": deleted,
        "process_flag_reset": reset_flag,
    }


def delete_linked_processes_on_spam(
    integration: IntegrationService,
    *,
    document_ref_key: str | None,
) -> dict[str, Any]:
    """Точка входа для mark_spam: без document_ref_key — no-op."""
    return delete_linked_processes_for_incoming_document(
        integration,
        document_ref_key=document_ref_key or "",
    )
