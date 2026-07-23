"""REST API агента-почты (ТЗ §12, human-in-the-loop §8)."""

from __future__ import annotations

import json
import re
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
from agent_pochta.stats.classification_log import (
    collect_classification_summary,
    collect_operator_approvals,
    operator_approval_fields_changed,
)
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
from agent_pochta.attachments.download import (
    content_disposition_header,
    fetch_attachment_for_download,
)
from agent_pochta.imap.body_fetch import fetch_and_cache_email_body, payload_body_text, row_has_cached_body
from agent_pochta.metrics.prometheus_exporter import refresh_prometheus_metrics
from agent_pochta.workers.tasks import (
    continue_after_human_task,
    reprocess_message_task,
    retry_erp_task,
    sync_erp_correction_task,
)
from agent_pochta.api.email_messages_export import collect_export_data
from agent_pochta.api.list_table_fields import row_to_table_fields

app = FastAPI(title="Agent-Pochta API", version="0.2.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class HumanResolveRequest(BaseModel):
    decision: str = Field(
        description="approve_routing | mark_verified | mark_spam | mark_not_spam"
    )
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
    routing_decision = payload.get("routing_decision")
    if not isinstance(routing_decision, dict):
        routing_decision = {}
    return {
        "to": to_list,
        "routing_recipient": str(routing_recipient).strip() if routing_recipient else None,
        "hitl_reason": str(hitl_reason).strip() if hitl_reason else None,
        "routing_decision": routing_decision,
        "rag_fallback": bool(payload.get("rag_fallback")) if "rag_fallback" in payload else None,
        "operator_verified": bool(payload.get("operator_verified")),
        "operator_corrected": bool(payload.get("operator_corrected")),
    }


_HITL_SCORE_RE = re.compile(r"score\s*=\s*(\d+)", re.IGNORECASE)


def _dept_confidence_for_api(row, meta: dict[str, Any]) -> float | None:
    """dept_confidence из БД; если 0 при известном score правил — восстановить score/100 для UI."""
    value = row.dept_confidence
    if value is not None and float(value) > 0:
        return float(value)

    decision = meta.get("routing_decision") or {}
    score = decision.get("confidence_score")
    try:
        score_int = int(score) if score is not None else 0
    except (TypeError, ValueError):
        score_int = 0
    if score_int > 0:
        return min(1.0, score_int / 100.0)

    hitl = meta.get("hitl_reason") or hitl_reason_from_row(row) or ""
    match = _HITL_SCORE_RE.search(hitl)
    if match:
        return min(1.0, int(match.group(1)) / 100.0)

    return float(value) if value is not None else None


def _effective_route_confidence(
    dept_confidence: float | None,
    decision: dict[str, Any],
) -> tuple[int | None, str | None]:
    """Согласовать score/level правил с dept_confidence LLM для UI."""
    route_level = decision.get("confidence_level")
    try:
        route_score = int(decision.get("confidence_score") or 0)
    except (TypeError, ValueError):
        route_score = 0

    if dept_confidence is not None and float(dept_confidence) > 0:
        from agent_pochta.routing.confidence import score_to_level

        llm_score = round(float(dept_confidence) * 100)
        score = max(llm_score, route_score)
        return score, score_to_level(score).value

    if route_score > 0:
        return route_score, route_level
    return route_score or None, route_level


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


def _row_to_list_dict(
    row,
    *,
    operator_event_hints: dict[str, bool] | None = None,
) -> dict[str, Any]:
    """Lightweight serializer for list endpoints (no body_text)."""
    payload = _load_raw_payload(row)
    meta = _payload_meta(row)
    decision = meta.get("routing_decision") or {}
    dept_conf = _dept_confidence_for_api(row, meta)
    route_score, route_level = _effective_route_confidence(dept_conf, decision)
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
        "dept_confidence": dept_conf,
        "route_confidence_level": route_level,
        "route_confidence_score": route_score,
        "priority": row.priority,
        "summary_ru": row.summary_ru,
        "erp_document_number": row.erp_document_number,
        "erp_task_id": row.erp_task_id,
        "human_review": row.human_review,
        "erp_retry_count": row.erp_retry_count,
        "attachments_count": row.attachments_count,
        "operator_verified": bool(meta.get("operator_verified")),
        **_row_partner_fields(row),
        **row_to_table_fields(row, payload=payload, operator_event_hints=operator_event_hints),
    }


def _row_attachments(row) -> list[dict[str, Any]]:
    """Вложения с выдержками извлечённого текста (для detail view).

    Если строки email_attachments пусты (сбой обработки), берём имена из raw_payload_json,
    чтобы UI мог показать кнопки скачивания.
    """
    payload_list = [
        item
        for item in (_load_raw_payload(row).get("attachments") or [])
        if isinstance(item, dict)
    ]
    payload_by_name = {
        item.get("filename"): item for item in payload_list if item.get("filename")
    }
    db_atts = list(row.attachments or [])
    items: list[dict[str, Any]] = []

    if db_atts:
        for index, att in enumerate(db_atts):
            meta = payload_by_name.get(att.filename) or {}
            items.append(
                {
                    "index": index,
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

    for index, meta in enumerate(payload_list):
        filename = str(meta.get("filename") or "").strip() or f"attachment-{index}"
        items.append(
            {
                "index": index,
                "filename": filename,
                "mime_type": meta.get("mime_type"),
                "size_bytes": meta.get("size_bytes"),
                "ocr_used": meta.get("ocr_used"),
                "has_text": meta.get("has_text", False),
                "text_excerpt": meta.get("text_excerpt"),
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
) -> dict[str, Any]:
    parsed_from, parsed_to = _message_list_filters(date_from=date_from, date_to=date_to)
    if parsed_from:
        approvals_start = msk_day_start_utc(parsed_from)
    else:
        approvals_start = None
    if parsed_to:
        approvals_end = msk_day_end_exclusive_utc(parsed_to)
    else:
        approvals_end = None

    with get_session_factory()() as session:
        repo = EmailRepository(session)
        filter_kwargs = {
            "date_from": parsed_from,
            "date_to": parsed_to,
            "search": q,
            "recipient_q": recipient_q,
            "info_recipient_only": info_recipient_only,
            "only_info_to_test_ii": only_info_to_test_ii,
            "only_info_to": only_info_to,
        }
        by_status = repo.count_by_status(**filter_kwargs)
        operator_review_counts = repo.count_operator_review_states(
            status=status,
            **filter_kwargs,
        )
        operator_approvals = collect_operator_approvals(
            session,
            start_utc=approvals_start,
            end_utc=approvals_end,
        )
        return {
            "total": sum(by_status.values()),
            "by_status": by_status,
            "operator_review_counts": operator_review_counts,
            "operator_approvals": operator_approvals,
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


@app.get("/api/v1/email-messages/export")
def export_email_messages_report(
    period: str = Query(default="day", pattern="^(day|week|month)$"),
) -> Response:
    """Excel-отчёт для «Таняфикации»: сводка + детализация писем за период (MSK)."""
    with get_session_factory()() as session:
        content, filename = collect_export_data(
            session,
            period=period,  # type: ignore[arg-type]
            row_to_list_dict=_row_to_list_dict,
        )
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": content_disposition_header(filename)},
    )


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
        event_hints = repo.batch_operator_review_event_hints([row.id for row in rows])
        return {
            "items": [
                _row_to_list_dict(row, operator_event_hints=event_hints.get(row.id))
                for row in rows
            ],
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

_ATTACHMENT_DOWNLOAD_ERROR_MESSAGES = {
    "not_found": "Письмо не найдено",
    "attachment_not_found": "Вложение не найдено",
    "not_in_mailbox": "Письмо не найдено в почтовом ящике (возможно, удалено)",
    "no_mailbox": "Не указан почтовый ящик для загрузки",
    "attachment_unavailable": "Файл вложения недоступен (удалён или слишком большой)",
}


def _fetch_body_error_detail(reason: str | None) -> str:
    if not reason:
        return "Не удалось загрузить текст письма"
    if reason in _FETCH_BODY_ERROR_MESSAGES:
        return _FETCH_BODY_ERROR_MESSAGES[reason]
    if reason.startswith("imap_error:"):
        return f"Ошибка IMAP: {reason.removeprefix('imap_error: ').strip()}"
    return reason


def _attachment_download_error_detail(reason: str | None) -> str:
    if not reason:
        return "Не удалось скачать вложение"
    if reason in _ATTACHMENT_DOWNLOAD_ERROR_MESSAGES:
        return _ATTACHMENT_DOWNLOAD_ERROR_MESSAGES[reason]
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


@app.get("/api/v1/email-messages/{row_id}/attachments/{index}")
def download_email_attachment(row_id: uuid.UUID, index: int) -> Response:
    """Скачивает вложение по индексу: байты подтягиваются из IMAP (в БД не хранятся)."""
    if index < 0:
        raise HTTPException(status_code=404, detail=_attachment_download_error_detail("attachment_not_found"))

    with get_session_factory()() as session:
        row = EmailRepository(session).get_by_id(row_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Message not found")
        if is_demo_message(message_id=row.message_id, sender_email=row.sender_email):
            raise HTTPException(status_code=404, detail="Message not found")

    result = fetch_attachment_for_download(row_id, index)

    if not result.ok or result.content is None:
        not_found_reasons = {
            "not_found",
            "attachment_not_found",
            "not_in_mailbox",
            "attachment_unavailable",
        }
        status_code = 404 if result.reason in not_found_reasons else 503
        raise HTTPException(
            status_code=status_code,
            detail=_attachment_download_error_detail(result.reason),
        )

    return Response(
        content=result.content,
        media_type=result.mime_type or "application/octet-stream",
        headers={"Content-Disposition": content_disposition_header(result.filename)},
    )


def _xml_download_filename(row_id: uuid.UUID) -> str:
    """Имя файла для скачивания XML: incoming_{short_id}.xml."""
    short = str(row_id).replace("-", "")[:8]
    return f"incoming_{short}.xml"


@app.get("/api/v1/email-messages/{row_id}/xml")
def download_email_xml(row_id: uuid.UUID) -> Response:
    """Отдаёт XML-документ из БД как вложение (без записи на диск)."""
    with get_session_factory()() as session:
        row = EmailRepository(session).get_by_id(row_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Message not found")
        if is_demo_message(message_id=row.message_id, sender_email=row.sender_email):
            raise HTTPException(status_code=404, detail="Message not found")
        xml_fields = _payload_xml_fields(row)
        xml = xml_fields.get("xml_document")

    if not isinstance(xml, str) or not xml.strip():
        raise HTTPException(status_code=404, detail="XML document not found")

    filename = _xml_download_filename(row_id)
    return Response(
        content=xml.encode("utf-8"),
        media_type="application/xml",
        headers={"Content-Disposition": content_disposition_header(filename)},
    )


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


def _schedule_erp_sync_if_needed(row) -> dict[str, Any]:
    """Планирует PATCH + догрузку вложений, если документ уже есть в 1С."""
    from agent_pochta.services.erp_attachments import existing_erp_document_ref_key

    if not existing_erp_document_ref_key(row):
        return {}
    task = sync_erp_correction_task.delay(row.message_id)
    return {"erp_sync_scheduled": True, "erp_sync_task_id": task.id}


def _apply_operator_routing_save(
    session,
    repo: EmailRepository,
    row,
    body: HumanResolveRequest,
    *,
    resolve_status: str,
    already_processed: bool,
) -> dict[str, Any]:
    """Сохраняет отдел/партнёра/орг., пишет operator_approvals и обучает маршрутизацию."""
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
    partner_override = normalize_partner_name(body.partner_name)
    previous_partner = partner_from_payload(
        row.raw_payload_json,
        sender_name=row.sender_name,
        sender_email=row.sender_email,
        summary_ru=row.summary_ru,
    )
    previous_xml = parse_document_xml(
        str((_load_raw_payload(row).get("xml_document") or ""))
    ) or {}
    previous_organization = normalize_organization_code(
        str(previous_xml.get("organization") or "")
    ) or "НП"
    new_organization = organization_override or previous_organization
    # Сравниваем partner только если оператор явно передал значение
    # (пустой/отсутствующий partner_name не считаем изменением).
    compare_partner = body.partner_name is not None
    fields_changed = operator_approval_fields_changed(
        old_department_id=original_department_id,
        new_department_id=body.department_id,
        old_partner=previous_partner,
        new_partner=partner_override if compare_partner else previous_partner,
        old_organization=previous_organization,
        new_organization=new_organization,
        compare_partner=compare_partner,
        compare_organization=body.organization is not None,
    )
    log_department_resolution(
        session,
        message_id=row.message_id,
        email_id=row.id,
        original_department_id=original_department_id,
        original_department_name=original_department_name,
        department_id=body.department_id,
        department_name=department_name,
        force_changed=fields_changed,
    )
    repo.set_operator_corrected(row, fields_changed)
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
        row.processed_at = datetime.now(timezone.utc).replace(tzinfo=None)
    if (
        partner_override
        and not body.contractor_id
        and is_valid_sender_email(row.sender_email)
    ):
        hitl_contractor_id = contractor_id_from_email(row.sender_email)
        hitl_email = row.sender_email.strip().lower()
        CatalogRepository(session).upsert_manual_contractor(
            contractor_id=hitl_contractor_id,
            name=partner_override,
            email=hitl_email,
            department_code=body.department_id,
        )
        from agent_pochta.routing.learning import enrich_hitl_contractor_in_qdrant

        enrich_hitl_contractor_in_qdrant(
            contractor_id=hitl_contractor_id,
            name=partner_override,
            email=hitl_email,
            department_code=body.department_id,
        )
    email = repo.load_email_from_row(row)
    learning: dict[str, Any] = {}
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
            partner=partner_override,
            organization=organization_override,
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
    return learning


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
            learning = _apply_operator_routing_save(
                session,
                repo,
                row,
                body,
                resolve_status=resolve_status,
                already_processed=already_processed,
            )
            session.commit()
            if already_processed:
                return {
                    "status": "correction_saved",
                    "id": str(row_id),
                    **_schedule_erp_sync_if_needed(row),
                    **learning,
                }
            task = continue_after_human_task.delay(str(row_id))
            return {
                "task_id": task.id,
                "status": "continuing",
                **learning,
            }
        elif body.decision == "mark_verified":
            # Подтверждение просмотра done/error: без смены статуса и без пайплайна.
            # (approve_routing на error → done; здесь статус сохраняем.)
            if row.status not in {
                ProcessingStatus.DONE.value,
                ProcessingStatus.ERROR.value,
                ProcessingStatus.DIALOG.value,
            }:
                raise HTTPException(
                    status_code=400,
                    detail="mark_verified allowed only for done, error, or dialog",
                )
            learning = _apply_operator_routing_save(
                session,
                repo,
                row,
                body,
                resolve_status=row.status,
                already_processed=True,
            )
            repo.set_operator_verified(row, True)
            session.commit()
            return {
                "status": "verified",
                "id": str(row_id),
                "operator_verified": True,
                **_schedule_erp_sync_if_needed(row),
                **learning,
            }
        else:
            raise HTTPException(status_code=400, detail="Unknown decision")

        session.commit()
        return {"status": "resolved", "id": str(row_id)}


@app.post("/api/v1/email-messages/{row_id}/reanalyze")
def reanalyze_message(row_id: uuid.UUID) -> dict[str, Any]:
    """Повторный LLM-анализ партнёра, отдела и организации (без пометки спамом / без ERP)."""
    allowed = {
        ProcessingStatus.AWAITING_HUMAN.value,
        ProcessingStatus.DONE.value,
        ProcessingStatus.ERROR.value,
        ProcessingStatus.PROCESSING.value,
    }
    with get_session_factory()() as session:
        repo = EmailRepository(session)
        row = repo.get_by_id(row_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Message not found")
        if row.status == ProcessingStatus.SPAM.value:
            raise HTTPException(
                status_code=400,
                detail="Use restore-from-spam for spam messages",
            )
        if row.status not in allowed:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot reanalyze message in status {row.status}",
            )
        email = repo.load_email_from_row(row)
        if email is None:
            raise HTTPException(status_code=400, detail="Message payload unavailable")
        row.status = ProcessingStatus.PROCESSING.value
        row.human_review = False
        repo.set_operator_verified(row, False)
        repo.set_operator_corrected(row, False)
        session.commit()

    task = reprocess_message_task.delay(str(row_id), reanalyze=True)
    return {
        "task_id": task.id,
        "status": "reanalyzing",
        "id": str(row_id),
    }


@app.post("/api/v1/email-messages/{row_id}/retry-erp")
def retry_erp(row_id: uuid.UUID) -> dict[str, Any]:
    with get_session_factory()() as session:
        repo = EmailRepository(session)
        row = repo.get_by_id(row_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Message not found")
        message_id = row.message_id
        # Ручной повтор с UI — сброс лимита автоматических попыток.
        row.erp_retry_count = 0
        row.human_review = False
        session.commit()

    task = retry_erp_task.delay(message_id)
    return {"task_id": task.id, "message_id": message_id, "status": "erp_retry_scheduled"}
