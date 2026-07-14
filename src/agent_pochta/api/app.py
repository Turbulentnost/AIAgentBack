"""REST API агента-почты (ТЗ §12, human-in-the-loop §8)."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel, Field

from agent_pochta.config import get_settings
from agent_pochta.demo_filter import is_demo_message
from agent_pochta.db.message_filters import (
    MSK,
    msk_day_end_exclusive_utc,
    msk_day_start_utc,
    parse_optional_date,
)
from agent_pochta.stats.classification_log import collect_classification_summary
from agent_pochta.db.catalog_repository import CatalogRepository
from agent_pochta.db.department_repository import DepartmentRepository
from agent_pochta.db.repository import EmailRepository
from agent_pochta.db.session import get_session_factory
from agent_pochta.routing.learning import (
    learn_from_routing_correction,
    learn_from_not_spam,
    learn_from_spam_mark,
)
from agent_pochta.routing.hitl import hitl_reason_from_row, row_requires_routing_review
from agent_pochta.stats.change_log import (
    log_department_resolution,
    log_restore_from_spam,
    log_spam_decision,
)
from agent_pochta.rules.spam_learning import resolve_human_spam_reason
from agent_pochta.email_payload import BODY_NOT_STORED_PLACEHOLDER
from agent_pochta.schemas import ProcessingStatus
from agent_pochta.routing.organizations import list_organizations_for_ui, normalize_organization_code
from agent_pochta.services.routing_departments import list_active_departments_for_ui
from agent_pochta.services.contractor_seed import contractor_id_from_email, is_valid_sender_email, partner_from_payload
from agent_pochta.services.llm_analyze import normalize_partner_name
from agent_pochta.services.rag_qdrant import search_contractors as qdrant_search_contractors
from agent_pochta.routing.xml_parser import parse_document_xml
from agent_pochta.imap.body_fetch import fetch_and_cache_email_body, payload_body_text, row_has_cached_body
from agent_pochta.metrics.prometheus_exporter import refresh_prometheus_metrics
from agent_pochta.workers.tasks import (
    continue_after_human_task,
    reprocess_message_task,
    retry_erp_task,
)

app = FastAPI(title="Agent-Pochta API", version="0.2.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class HumanResolveRequest(BaseModel):
    decision: str = Field(description="approve_routing | mark_spam | mark_not_spam")
    department_id: str | None = None
    department_name: str | None = None
    partner_name: str | None = None
    contractor_id: str | None = None
    process: str | None = None
    organization: str | None = None


def _load_raw_payload(row) -> dict[str, Any]:
    if not row.raw_payload_json:
        return {}
    try:
        payload = json.loads(row.raw_payload_json)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _payload_meta(row) -> dict[str, Any]:
    payload = _load_raw_payload(row)
    to_raw = payload.get("to") or []
    to_list = [str(item).strip() for item in to_raw if str(item).strip()]
    routing_recipient = payload.get("routing_recipient")
    hitl_reason = payload.get("hitl_reason")
    return {
        "to": to_list,
        "routing_recipient": str(routing_recipient).strip() if routing_recipient else None,
        "hitl_reason": str(hitl_reason).strip() if hitl_reason else None,
    }


def _utc_iso(value: datetime | None) -> str | None:
    """Serialize naive UTC timestamps for JSON (ISO 8601 with Z suffix)."""
    if value is None:
        return None
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return f"{value.isoformat()}Z"


def _payload_xml_fields(row) -> dict[str, Any]:
    payload = _load_raw_payload(row)
    xml = payload.get("xml_document")
    if not isinstance(xml, str) or not xml.strip():
        return {"xml_document": None, "document_xml": None}
    return {
        "xml_document": xml,
        "document_xml": parse_document_xml(xml),
    }


def _payload_body_text(row) -> str:
    payload = _load_raw_payload(row)
    body = str(payload.get("body_text") or "").strip()
    if body:
        return body
    body_html = payload.get("body_html")
    if body_html:
        from agent_pochta.imap.parser import _html_to_text

        return _html_to_text(str(body_html))
    return BODY_NOT_STORED_PLACEHOLDER


def _row_partner_fields(row) -> dict[str, Any]:
    """Партнёр для UI: из XML, иначе sender_name."""
    partner_name = partner_from_payload(
        row.raw_payload_json,
        sender_name=row.sender_name,
        sender_email=row.sender_email,
        summary_ru=row.summary_ru,
    )
    return {
        "contractor_id": row.contractor_id,
        "is_new_contractor": row.is_new_contractor,
        "partner_name": partner_name,
    }


def _row_to_list_dict(row) -> dict[str, Any]:
    """Lightweight serializer for list endpoints (no body_text)."""
    meta = _payload_meta(row)
    return {
        "id": str(row.id),
        "message_id": row.message_id,
        "received_at": _utc_iso(row.received_at),
        "processed_at": _utc_iso(row.processed_at),
        "mailbox": row.mailbox,
        "sender_email": row.sender_email,
        "sender_name": row.sender_name,
        "to": meta["to"],
        "routing_recipient": meta["routing_recipient"],
        "subject": row.subject,
        "status": row.status,
        "is_spam": row.is_spam,
        "spam_confidence": row.spam_confidence,
        "spam_reason": row.spam_reason,
        "hitl_reason": meta.get("hitl_reason") or hitl_reason_from_row(row),
        "department_id": row.department_id,
        "department_name": row.department_name,
        "dept_confidence": row.dept_confidence,
        "priority": row.priority,
        "summary_ru": row.summary_ru,
        "erp_document_number": row.erp_document_number,
        "erp_task_id": row.erp_task_id,
        "human_review": row.human_review,
        "erp_retry_count": row.erp_retry_count,
        "attachments_count": row.attachments_count,
        **_row_partner_fields(row),
    }


def _row_attachments(row) -> list[dict[str, Any]]:
    """Вложения с выдержками извлечённого текста (для detail view)."""
    payload_attachments = {
        item.get("filename"): item
        for item in (_load_raw_payload(row).get("attachments") or [])
        if isinstance(item, dict) and item.get("filename")
    }
    items: list[dict[str, Any]] = []
    for att in row.attachments:
        meta = payload_attachments.get(att.filename) or {}
        items.append(
            {
                "filename": att.filename,
                "mime_type": att.mime_type,
                "size_bytes": att.size_bytes,
                "ocr_used": att.ocr_used,
                "has_text": meta.get("has_text", bool(att.extracted_text)),
                "text_excerpt": att.extracted_text or meta.get("text_excerpt"),
                "extraction_error": meta.get("extraction_error"),
            }
        )
    return items


def _row_to_dict(row) -> dict[str, Any]:
    return {
        **_row_to_list_dict(row),
        "body_text": _payload_body_text(row),
        "attachments": _row_attachments(row),
        **_payload_xml_fields(row),
    }


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/metrics")
def metrics() -> Response:
    """Prometheus scrape endpoint (обновляет Gauges из PostgreSQL при каждом запросе)."""
    from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

    refresh_prometheus_metrics()
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/api/v1/departments")
def list_routing_departments() -> list[dict[str, str]]:
    """Справочник отделов для UI: названия из структуры 1С."""
    return list_active_departments_for_ui()


@app.get("/api/v1/organizations")
def list_organizations() -> list[dict[str, str]]:
    return list_organizations_for_ui()


def _contractor_matches_query(name: str, emails: list[str], query: str) -> bool:
    q = query.strip().lower()
    if not q:
        return False
    if q in name.lower():
        return True
    return any(q in email.lower() for email in emails)


@app.get("/api/v1/contractors/search")
def search_contractors_endpoint(
    q: str = Query(..., min_length=2, max_length=200),
    limit: int = Query(default=20, ge=1, le=50),
) -> list[dict[str, Any]]:
    """Автодополнение контрагентов по имени или email."""
    settings = get_settings()
    seen: set[str] = set()
    results: list[dict[str, Any]] = []

    if settings.rag_backend == "qdrant":
        for item in qdrant_search_contractors(settings.qdrant_url, q, limit=limit):
            key = item.get("contractor_id") or item.get("email") or ""
            if not key or key in seen:
                continue
            seen.add(key)
            results.append(item)

    with get_session_factory()() as session:
        for contractor in CatalogRepository(session).load_active_contractors():
            if len(results) >= limit:
                break
            if contractor.contractor_id in seen:
                continue
            if not _contractor_matches_query(contractor.name, contractor.emails, q):
                continue
            seen.add(contractor.contractor_id)
            primary_email = contractor.emails[0] if contractor.emails else ""
            results.append(
                {
                    "contractor_id": contractor.contractor_id,
                    "name": contractor.name,
                    "email": primary_email,
                    "emails": contractor.emails,
                    "contractor_type": contractor.contractor_type,
                }
            )

    results.sort(key=lambda item: (str(item.get("name") or "").lower(), str(item.get("email") or "")))
    return results[:limit]


def _message_list_filters(
    *,
    date_from: str | None,
    date_to: str | None,
) -> tuple[Any, Any]:
    parsed_from = parse_optional_date(date_from)
    parsed_to = parse_optional_date(date_to)
    if parsed_from and parsed_to and parsed_from > parsed_to:
        raise HTTPException(status_code=400, detail="date_from must be <= date_to")
    return parsed_from, parsed_to


@app.get("/api/v1/email-messages/stats")
def email_messages_stats(
    date_from: str | None = Query(default=None, description="YYYY-MM-DD (MSK)"),
    date_to: str | None = Query(default=None, description="YYYY-MM-DD (MSK)"),
    q: str | None = Query(default=None, min_length=1, max_length=200),
    recipient_q: str | None = Query(
        default=None,
        min_length=1,
        max_length=200,
        description="Поиск только по графе «Кому» (routing_recipient / To)",
    ),
    info_recipient_only: bool = Query(
        default=False,
        description="Только письма, где «Кому» содержит info (Outlook: имяполучателя:(info))",
    ),
    only_info_to_test_ii: bool = Query(
        default=False,
        description="Только письма по цепочке info@turbo-don.ru → test_ii@turbo-don.ru",
    ),
    only_info_to: bool = Query(
        default=False,
        description="Только письма с получателем info@turbo-don.ru (Кому), без других адресов",
    ),
) -> dict[str, Any]:
    parsed_from, parsed_to = _message_list_filters(date_from=date_from, date_to=date_to)
    with get_session_factory()() as session:
        repo = EmailRepository(session)
        by_status = repo.count_by_status(
            date_from=parsed_from,
            date_to=parsed_to,
            search=q,
            recipient_q=recipient_q,
            info_recipient_only=info_recipient_only,
            only_info_to_test_ii=only_info_to_test_ii,
            only_info_to=only_info_to,
        )
        return {
            "total": sum(by_status.values()),
            "by_status": by_status,
        }


@app.get("/api/v1/classification-events/summary")
def classification_events_summary(
    date_from: str | None = Query(default=None, description="YYYY-MM-DD (MSK)"),
    date_to: str | None = Query(default=None, description="YYYY-MM-DD (MSK)"),
) -> dict[str, Any]:
    """Агрегаты classification_events для графиков и оценки точности."""
    parsed_from, parsed_to = _message_list_filters(date_from=date_from, date_to=date_to)
    settings = get_settings()
    if parsed_from:
        start_utc = msk_day_start_utc(parsed_from)
    else:
        raw = settings.stats_start_time.strip()
        try:
            start_local = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S").replace(tzinfo=MSK)
        except ValueError:
            start_local = datetime.fromisoformat(raw).replace(tzinfo=MSK)
        start_utc = start_local.astimezone(timezone.utc).replace(tzinfo=None)

    if parsed_to:
        end_utc = msk_day_end_exclusive_utc(parsed_to)
    else:
        end_utc = datetime.now(timezone.utc).replace(tzinfo=None)

    with get_session_factory()() as session:
        return collect_classification_summary(session, start_utc=start_utc, end_utc=end_utc)


@app.get("/api/v1/email-messages")
def list_email_messages(
    status: str | None = Query(default=None),
    date_from: str | None = Query(default=None, description="YYYY-MM-DD (MSK)"),
    date_to: str | None = Query(default=None, description="YYYY-MM-DD (MSK)"),
    q: str | None = Query(default=None, min_length=1, max_length=200),
    recipient_q: str | None = Query(
        default=None,
        min_length=1,
        max_length=200,
        description="Поиск только по графе «Кому» (routing_recipient / To)",
    ),
    info_recipient_only: bool = Query(
        default=False,
        description="Только письма, где «Кому» содержит info (Outlook: имяполучателя:(info))",
    ),
    only_info_to_test_ii: bool = Query(
        default=False,
        description="Только письма по цепочке info@turbo-don.ru → test_ii@turbo-don.ru",
    ),
    only_info_to: bool = Query(
        default=False,
        description="Только письма с получателем info@turbo-don.ru (Кому), без других адресов",
    ),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    parsed_from, parsed_to = _message_list_filters(date_from=date_from, date_to=date_to)
    with get_session_factory()() as session:
        repo = EmailRepository(session)
        rows = repo.list_messages(
            status=status,
            date_from=parsed_from,
            date_to=parsed_to,
            search=q,
            recipient_q=recipient_q,
            info_recipient_only=info_recipient_only,
            only_info_to_test_ii=only_info_to_test_ii,
            only_info_to=only_info_to,
            limit=limit,
            offset=offset,
        )
        total = repo.count_messages(
            status=status,
            date_from=parsed_from,
            date_to=parsed_to,
            search=q,
            recipient_q=recipient_q,
            info_recipient_only=info_recipient_only,
            only_info_to_test_ii=only_info_to_test_ii,
            only_info_to=only_info_to,
        )
        return {
            "items": [_row_to_list_dict(row) for row in rows],
            "total": total,
            "limit": limit,
            "offset": offset,
        }


@app.get("/api/v1/email-messages/{row_id}")
def get_email_message(row_id: uuid.UUID) -> dict[str, Any]:
    with get_session_factory()() as session:
        row = EmailRepository(session).get_by_id(row_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Message not found")
        if is_demo_message(message_id=row.message_id, sender_email=row.sender_email):
            raise HTTPException(status_code=404, detail="Message not found")
        return _row_to_dict(row)


_FETCH_BODY_ERROR_MESSAGES = {
    "not_found": "Письмо не найдено",
    "not_in_mailbox": "Письмо не найдено в почтовом ящике (возможно, удалено)",
    "no_mailbox": "Не указан почтовый ящик для загрузки",
    "empty_body": "Текст письма пуст",
}


def _fetch_body_error_detail(reason: str | None) -> str:
    if not reason:
        return "Не удалось загрузить текст письма"
    if reason in _FETCH_BODY_ERROR_MESSAGES:
        return _FETCH_BODY_ERROR_MESSAGES[reason]
    if reason.startswith("imap_error:"):
        return f"Ошибка IMAP: {reason.removeprefix('imap_error: ').strip()}"
    return reason


@app.post("/api/v1/email-messages/{row_id}/fetch-body")
def fetch_email_body(row_id: uuid.UUID) -> dict[str, Any]:
    """Загружает тело письма из IMAP и кеширует в raw_payload_json.

    Выполняется синхронно в API-процессе (FastAPI threadpool), без Celery:
    очередь worker часто занята долгими process_email, а rpc:// result backend
    ненадёжен между контейнерами.
    """
    with get_session_factory()() as session:
        row = EmailRepository(session).get_by_id(row_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Message not found")

        if row_has_cached_body(row):
            payload = json.loads(row.raw_payload_json or "{}")
            return {
                "status": "ready",
                "id": str(row_id),
                "body_text": payload_body_text(payload),
                "cached": True,
            }

    result = fetch_and_cache_email_body(row_id)

    if not result.ok:
        status_code = 404 if result.reason in {"not_found", "not_in_mailbox", "empty_body"} else 503
        raise HTTPException(
            status_code=status_code,
            detail=_fetch_body_error_detail(result.reason),
        )

    return {
        "status": "ready",
        "id": str(row_id),
        "body_text": result.body_text or "",
        "cached": result.cached,
    }


@app.post("/api/v1/email-messages/{row_id}/restore-from-spam")
def restore_from_spam(row_id: uuid.UUID) -> dict[str, Any]:
    with get_session_factory()() as session:
        repo = EmailRepository(session)
        row = repo.get_by_id(row_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Message not found")
        if row.status != ProcessingStatus.SPAM.value:
            raise HTTPException(status_code=400, detail="Message is not marked as spam")
        email = repo.load_email_from_row(row)
        if email is None:
            raise HTTPException(status_code=400, detail="Message payload unavailable")
        learn_from_not_spam(
            message_id=row.message_id,
            sender_email=email.sender_email,
            subject=email.subject or "",
            body=repo.learning_text_from_row(row, email),
            reason="Восстановлено из спама оператором",
            session=session,
            email_id=row.id,
        )
        log_restore_from_spam(
            session,
            message_id=row.message_id,
            email_id=row.id,
        )
        repo.mark_restored_from_spam(row.id)
        session.commit()

    task = reprocess_message_task.delay(str(row_id), restored_from_spam=True)
    return {
        "task_id": task.id,
        "status": ProcessingStatus.AWAITING_HUMAN.value,
        "id": str(row_id),
        "restored_from_spam": True,
    }


@app.post("/api/v1/email-messages/{row_id}/resolve-human")
def resolve_human(row_id: uuid.UUID, body: HumanResolveRequest) -> dict[str, Any]:
    with get_session_factory()() as session:
        repo = EmailRepository(session)
        row = repo.get_by_id(row_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Message not found")

        if body.decision == "mark_spam":
            spam_reason = resolve_human_spam_reason(hitl_reason_from_row(row))
            log_spam_decision(
                session,
                message_id=row.message_id,
                email_id=row.id,
                decision="mark_spam",
                reason=spam_reason,
                old_is_spam=row.is_spam,
            )
            repo.apply_human_resolution(row.id, status=ProcessingStatus.SPAM.value, is_spam=True)
            repo.clear_xml_document(row)
            email = repo.load_email_from_row(row)
            learning: dict[str, Any] = {}
            if email is not None:
                learning = learn_from_spam_mark(
                    message_id=row.message_id,
                    sender_email=email.sender_email,
                    subject=email.subject or "",
                    body=repo.learning_text_from_row(row, email),
                    spam_reason=spam_reason,
                    session=session,
                    email_id=row.id,
                )
            session.commit()
            return {
                "status": "resolved",
                "id": str(row_id),
                "spam_pattern_saved": learning.get("spam_pattern_saved", False),
                "spam_pattern_id": learning.get("spam_pattern_id"),
                "qdrant_synced": learning.get("qdrant_synced", False),
            }
        elif body.decision == "mark_not_spam":
            log_spam_decision(
                session,
                message_id=row.message_id,
                email_id=row.id,
                decision="mark_not_spam",
                reason="Отмечено офис-менеджером как не спам",
                old_is_spam=row.is_spam,
            )
            email = repo.load_email_from_row(row)
            learning: dict[str, Any] = {}
            if email is not None and not row_requires_routing_review(row):
                learning = learn_from_not_spam(
                    message_id=row.message_id,
                    sender_email=email.sender_email,
                    subject=email.subject or "",
                    body=repo.learning_text_from_row(row, email),
                    reason="Отмечено офис-менеджером как не спам",
                    session=session,
                    email_id=row.id,
                )
            repo.apply_human_resolution(
                row.id, status=ProcessingStatus.PROCESSING.value, is_spam=False
            )
            repo.clear_xml_document(row)
            session.commit()
            task = reprocess_message_task.delay(str(row_id))
            return {
                "task_id": task.id,
                "status": "reprocessing",
                **learning,
            }
        elif body.decision == "approve_routing":
            if not body.department_id:
                raise HTTPException(status_code=400, detail="department_id required")
            if row.status == ProcessingStatus.SPAM.value:
                raise HTTPException(
                    status_code=400,
                    detail="Cannot change department on spam messages",
                )
            original_department_id = row.department_id
            original_department_name = row.department_name
            department_name = body.department_name or body.department_id
            organization_override = normalize_organization_code(body.organization)
            if body.organization and organization_override is None:
                raise HTTPException(status_code=400, detail="Unknown organization code")
            already_processed = row.status in {
                ProcessingStatus.DONE.value,
                ProcessingStatus.ERROR.value,
            }
            if already_processed:
                resolve_status = (
                    ProcessingStatus.DONE.value
                    if row.status == ProcessingStatus.ERROR.value
                    else row.status
                )
            else:
                resolve_status = ProcessingStatus.PROCESSING.value
            log_department_resolution(
                session,
                message_id=row.message_id,
                email_id=row.id,
                original_department_id=original_department_id,
                original_department_name=original_department_name,
                department_id=body.department_id,
                department_name=department_name,
            )
            partner_override = normalize_partner_name(body.partner_name)
            repo.apply_human_resolution(
                row.id,
                status=resolve_status,
                department_id=body.department_id,
                department_name=department_name,
                is_spam=None if already_processed else False,
                contractor_id=body.contractor_id,
                partner_name=body.partner_name,
            )
            if resolve_status == ProcessingStatus.DONE.value and not row.processed_at:
                from datetime import datetime, timezone

                row.processed_at = datetime.now(timezone.utc).replace(tzinfo=None)
            if (
                partner_override
                and not body.contractor_id
                and is_valid_sender_email(row.sender_email)
            ):
                CatalogRepository(session).upsert_manual_contractor(
                    contractor_id=contractor_id_from_email(row.sender_email),
                    name=partner_override,
                    email=row.sender_email.strip().lower(),
                    department_code=body.department_id,
                )
            email = repo.load_email_from_row(row)
            learning = {}
            if email is not None:
                learning = learn_from_routing_correction(
                    message_id=row.message_id,
                    sender_email=email.sender_email,
                    recipient=email.routing_recipient or email.mailbox,
                    subject=email.subject,
                    body=repo.learning_text_from_row(row, email),
                    department_id=body.department_id,
                    department_name=department_name,
                    original_department_id=original_department_id,
                    original_department_name=original_department_name,
                    session=session,
                )
                from agent_pochta.routing.process_type import normalize_process_type

                process_override = normalize_process_type(body.process)
                repo.rebuild_xml_after_human_correction(
                    row,
                    email,
                    original_department_id=original_department_id,
                    original_department_name=original_department_name,
                    partner_override=partner_override,
                    process_override=process_override,
                    organization_override=organization_override,
                )
            session.commit()
            if already_processed:
                return {
                    "status": "correction_saved",
                    "id": str(row_id),
                    **learning,
                }
            task = continue_after_human_task.delay(str(row_id))
            return {
                "task_id": task.id,
                "status": "continuing",
                **learning,
            }
        else:
            raise HTTPException(status_code=400, detail="Unknown decision")

        session.commit()
        return {"status": "resolved", "id": str(row_id)}


@app.post("/api/v1/email-messages/{row_id}/retry-erp")
def retry_erp(row_id: uuid.UUID) -> dict[str, Any]:
    with get_session_factory()() as session:
        row = EmailRepository(session).get_by_id(row_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Message not found")
        message_id = row.message_id

    task = retry_erp_task.delay(message_id)
    return {"task_id": task.id, "message_id": message_id, "status": "erp_retry_scheduled"}
