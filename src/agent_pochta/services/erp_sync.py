"""Синхронизация существующего документа 1С после коррекции оператора."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import structlog

from agent_pochta.db.models import EmailMessageRow
from agent_pochta.schemas import EmailMessage, RoutingResult
from agent_pochta.services.erp_attachments import (
    attach_missing_email_files_to_document,
    clear_erp_attachment_entries,
    existing_erp_document_ref_key,
    merge_erp_attachment_lists,
    resolve_skip_filenames_for_erp_sync,
    uploaded_erp_attachment_filenames,
)
from agent_pochta.services.integration_service import IntegrationService

logger = structlog.get_logger(__name__)


def merge_erp_sync_meta_into_payload(
    raw_payload_json: str | None,
    sync_meta: dict[str, Any],
) -> str | None:
    if not sync_meta or not raw_payload_json:
        return raw_payload_json
    try:
        payload = json.loads(raw_payload_json)
    except json.JSONDecodeError:
        return raw_payload_json
    if not isinstance(payload, dict):
        return raw_payload_json

    new_attachments = sync_meta.get("erp_attachments")
    if isinstance(new_attachments, list):
        existing = payload.get("erp_attachments")
        payload["erp_attachments"] = merge_erp_attachment_lists(
            existing if isinstance(existing, list) else None,
            new_attachments,
        )
        sync_meta = {key: value for key, value in sync_meta.items() if key != "erp_attachments"}

    payload.update(sync_meta)
    return json.dumps(payload, ensure_ascii=False)


def _supports_document_update(integration: IntegrationService) -> bool:
    update_fn = getattr(integration, "update_incoming_correspondence", None)
    if not callable(update_fn):
        return False
    base_fn = IntegrationService.update_incoming_correspondence
    return getattr(update_fn, "__func__", update_fn) is not base_fn


def sync_existing_erp_document(
    *,
    message_id: str,
    row: EmailMessageRow,
    email: EmailMessage,
    routing: RoutingResult,
    summary_ru: str,
    integration: IntegrationService,
    vault,
    xml_document: str | None = None,
    force_reattach_filenames: set[str] | None = None,
) -> dict[str, Any]:
    """PATCH полей документа + догрузка недостающих вложений."""
    doc_ref = existing_erp_document_ref_key(row)
    if not doc_ref:
        return {"ok": False, "reason": "no_existing_document"}

    sync_errors: list[str] = []
    updated = False
    attached: list[dict] = []

    xml = (xml_document or "").strip() or None
    if _supports_document_update(integration):
        if not xml:
            sync_errors.append("missing_xml_document")
        elif not (summary_ru or "").strip():
            sync_errors.append("missing_summary_ru")
        else:
            try:
                result = integration.update_incoming_correspondence(
                    doc_ref,
                    email,
                    routing,
                    summary_ru,
                    xml_document=xml,
                )
                updated = bool(result.get("updated"))
            except Exception as exc:
                logger.exception(
                    "erp_document_update_failed",
                    message_id=message_id,
                    document_ref_key=doc_ref,
                )
                sync_errors.append(f"update: {exc}")
    else:
        logger.info(
            "erp_document_update_skipped",
            reason="integration_does_not_support_update",
            message_id=message_id,
        )

    skip_filenames = resolve_skip_filenames_for_erp_sync(
        row.raw_payload_json,
        force_reattach_filenames=force_reattach_filenames,
    )
    payload_modified = False
    if force_reattach_filenames:
        cleared = clear_erp_attachment_entries(
            row.raw_payload_json,
            filenames=force_reattach_filenames,
        )
        if cleared is not None and cleared != row.raw_payload_json:
            row.raw_payload_json = cleared
            payload_modified = True
    try:
        attached = attach_missing_email_files_to_document(
            integration,
            document_ref_key=doc_ref,
            email=email,
            vault=vault,
            skip_filenames=skip_filenames,
            erp_document_number=row.erp_document_number,
        )
    except Exception as exc:
        logger.exception(
            "erp_attach_missing_failed",
            message_id=message_id,
            document_ref_key=doc_ref,
        )
        sync_errors.append(f"attach: {exc}")

    sync_meta: dict[str, Any] = {
        "erp_last_sync_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
    }
    if sync_errors:
        sync_meta["erp_sync_errors"] = sync_errors
    else:
        sync_meta["erp_sync_errors"] = None
    if attached:
        sync_meta["erp_attachments"] = attached

    ok = updated or bool(attached) or (not sync_errors and not skip_filenames)
    if sync_errors and not updated and not attached:
        ok = False

    return {
        "ok": ok,
        "sync_existing": True,
        "erp_document_number": row.erp_document_number,
        "erp_document_id": doc_ref,
        "updated": updated,
        "attached_count": len(attached),
        "erp_attachments": attached or None,
        "erp_sync_meta": sync_meta,
        "raw_payload_json": row.raw_payload_json if payload_modified else None,
        **({"reason": sync_errors[0]} if sync_errors and not ok else {}),
    }
