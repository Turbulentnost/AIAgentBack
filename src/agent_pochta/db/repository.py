"""Сохранение и чтение результатов обработки письма в PostgreSQL (раздел 7 ТЗ)."""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timezone

import structlog
from sqlalchemy import and_, case, func, not_, or_
from sqlalchemy.orm import Session

from agent_pochta.db.message_filters import (
    compute_is_info_recipient,
    msk_day_end_exclusive_utc,
    msk_day_start_utc,
    info_to_test_ii_sql_filter,
    only_info_to_sql_filter,
    operator_review_state_sql_flags,
    recipient_q_sql_filter,
    routing_base_message_id_sql,
    safe_payload_jsonb,
    turbo_don_routing_sql_filter,
)
from agent_pochta.demo_filter import demo_row_filter

from agent_pochta.config import get_settings
from agent_pochta.routing.hitl import is_routing_escalation_reason
from agent_pochta.db.models import EmailAttachmentRow, EmailMessageRow
from agent_pochta.db.session import get_session_factory
from agent_pochta.email_payload import email_to_task_payload
from agent_pochta.schemas import EmailMessage, Priority, ProcessingStatus, RoutingResult
from agent_pochta.services.erp_attachments import (
    STUB_ERP_DOCUMENT_PREFIX,
    STUB_ERP_TASK_PREFIX,
)
from agent_pochta.state import AgentState

logger = structlog.get_logger(__name__)


def _sanitize_pg_text(value: str | None) -> str | None:
    """PostgreSQL text columns reject NUL (0x00) bytes."""
    if not value or "\x00" not in value:
        return value
    return value.replace("\x00", "")


class EmailRepository:
    """Upsert и чтение записей email_messages."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_by_id(self, row_id: uuid.UUID) -> EmailMessageRow | None:
        return self._session.get(EmailMessageRow, row_id)

    def get_by_message_id(self, message_id: str) -> EmailMessageRow | None:
        return self._session.query(EmailMessageRow).filter_by(message_id=message_id).one_or_none()

    def _apply_message_filters(
        self,
        query,
        *,
        status: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        search: str | None = None,
        recipient_q: str | None = None,
        info_recipient_only: bool = False,
        only_info_to_test_ii: bool = False,
        only_info_to: bool = False,
    ):
        if status:
            query = query.filter(EmailMessageRow.status == status)
        if date_from is not None:
            query = query.filter(EmailMessageRow.received_at >= msk_day_start_utc(date_from))
        if date_to is not None:
            query = query.filter(EmailMessageRow.received_at < msk_day_end_exclusive_utc(date_to))
        if search:
            pattern = f"%{search.strip()}%"
            query = query.filter(
                or_(
                    EmailMessageRow.subject.ilike(pattern),
                    EmailMessageRow.sender_email.ilike(pattern),
                    EmailMessageRow.sender_name.ilike(pattern),
                )
            )
        if recipient_q:
            query = query.filter(
                recipient_q_sql_filter(
                    EmailMessageRow.mailbox,
                    EmailMessageRow.raw_payload_json,
                    recipient_q,
                )
            )
        if info_recipient_only:
            query = query.filter(EmailMessageRow.is_info_recipient.is_(True))
        if only_info_to_test_ii:
            query = query.filter(
                info_to_test_ii_sql_filter(
                    EmailMessageRow.mailbox,
                    EmailMessageRow.raw_payload_json,
                )
            )
        if only_info_to:
            query = query.filter(
                only_info_to_sql_filter(
                    EmailMessageRow.mailbox,
                    EmailMessageRow.raw_payload_json,
                )
            )
        query = query.filter(
            turbo_don_routing_sql_filter(
                EmailMessageRow.mailbox,
                EmailMessageRow.raw_payload_json,
            )
        )
        query = query.filter(~demo_row_filter(EmailMessageRow))
        return query

    def _routing_dedupe_ranked_subquery(self, query):
        """ROW_NUMBER() по базовому Message-ID — основа dedupe-фильтра."""
        base_id = routing_base_message_id_sql(EmailMessageRow.message_id)
        payload = safe_payload_jsonb(EmailMessageRow.raw_payload_json)
        routing = func.lower(func.coalesce(payload["routing_recipient"].astext, ""))
        mailbox_l = func.lower(EmailMessageRow.mailbox)
        prefer_mailbox = case((routing == mailbox_l, 0), else_=1)
        return (
            query.with_entities(
                EmailMessageRow.id.label("row_id"),
                func.row_number()
                .over(
                    partition_by=base_id,
                    order_by=(prefer_mailbox.asc(), EmailMessageRow.received_at.desc()),
                )
                .label("rn"),
            )
        ).subquery()

    def _deduped_messages_cte(self, filtered_query):
        """CTE id строк после dedupe — один проход window function на запрос."""
        ranked = self._routing_dedupe_ranked_subquery(filtered_query)
        return (
            self._session.query(ranked.c.row_id.label("row_id"))
            .filter(ranked.c.rn == 1)
            .cte("deduped_message_ids")
        )

    def _apply_routing_dedupe(self, query):
        """Одна строка на физическое письмо (без дублей рассылки по #recipient@)."""
        ranked = self._routing_dedupe_ranked_subquery(query)
        return query.filter(
            EmailMessageRow.id.in_(
                self._session.query(ranked.c.row_id).filter(ranked.c.rn == 1)
            )
        )

    def list_messages_paginated(
        self,
        *,
        status: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        search: str | None = None,
        recipient_q: str | None = None,
        info_recipient_only: bool = False,
        only_info_to_test_ii: bool = False,
        only_info_to: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[EmailMessageRow], int]:
        """Список + total за один SQL (COUNT(*) OVER(), без второго dedupe-count)."""
        filtered = self._session.query(EmailMessageRow)
        filtered = self._apply_message_filters(
            filtered,
            status=status,
            date_from=date_from,
            date_to=date_to,
            search=search,
            recipient_q=recipient_q,
            info_recipient_only=info_recipient_only,
            only_info_to_test_ii=only_info_to_test_ii,
            only_info_to=only_info_to,
        )
        ranked = self._routing_dedupe_ranked_subquery(filtered)
        page_rows = (
            self._session.query(
                EmailMessageRow,
                func.count().over().label("total_count"),
            )
            .join(ranked, EmailMessageRow.id == ranked.c.row_id)
            .filter(ranked.c.rn == 1)
            .order_by(EmailMessageRow.received_at.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )
        if not page_rows:
            total = (
                self._session.query(func.count())
                .select_from(
                    self._session.query(ranked.c.row_id)
                    .filter(ranked.c.rn == 1)
                    .subquery()
                )
                .scalar()
                or 0
            )
            return [], int(total)
        return [row for row, _total in page_rows], int(page_rows[0][1])

    def message_stats_bundle(
        self,
        *,
        status: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        search: str | None = None,
        recipient_q: str | None = None,
        info_recipient_only: bool = False,
        only_info_to_test_ii: bool = False,
        only_info_to: bool = False,
    ) -> tuple[dict[str, int], dict[str, int]]:
        """by_status и operator_review_counts с одним dedupe CTE."""
        filtered = self._session.query(EmailMessageRow)
        filtered = self._apply_message_filters(
            filtered,
            date_from=date_from,
            date_to=date_to,
            search=search,
            recipient_q=recipient_q,
            info_recipient_only=info_recipient_only,
            only_info_to_test_ii=only_info_to_test_ii,
            only_info_to=only_info_to,
        )
        deduped_cte = self._deduped_messages_cte(filtered)
        messages = self._session.query(EmailMessageRow).join(
            deduped_cte, EmailMessageRow.id == deduped_cte.c.row_id
        )

        by_status_rows = (
            messages.with_entities(EmailMessageRow.status, func.count(EmailMessageRow.id))
            .group_by(EmailMessageRow.status)
            .all()
        )
        by_status = {status_value: int(count) for status_value, count in by_status_rows}

        review_query = messages
        if status:
            review_query = review_query.filter(EmailMessageRow.status == status)
        is_corrected, is_verified, is_pending = operator_review_state_sql_flags(
            EmailMessageRow.raw_payload_json,
            EmailMessageRow.id,
        )
        review_row = review_query.with_entities(
            func.count(EmailMessageRow.id).label("all"),
            func.sum(case((is_corrected, 1), else_=0)).label("corrected"),
            func.sum(case((is_verified, 1), else_=0)).label("verified"),
            func.sum(case((is_pending, 1), else_=0)).label("pending"),
        ).one()
        review_counts = {
            "all": int(review_row.all or 0),
            "corrected": int(review_row.corrected or 0),
            "verified": int(review_row.verified or 0),
            "pending": int(review_row.pending or 0),
        }
        return by_status, review_counts

    def list_messages(
        self,
        *,
        status: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        search: str | None = None,
        recipient_q: str | None = None,
        info_recipient_only: bool = False,
        only_info_to_test_ii: bool = False,
        only_info_to: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> list[EmailMessageRow]:
        query = self._session.query(EmailMessageRow).order_by(EmailMessageRow.received_at.desc())
        query = self._apply_message_filters(
            query,
            status=status,
            date_from=date_from,
            date_to=date_to,
            search=search,
            recipient_q=recipient_q,
            info_recipient_only=info_recipient_only,
            only_info_to_test_ii=only_info_to_test_ii,
            only_info_to=only_info_to,
        )
        query = self._apply_routing_dedupe(query)
        return query.offset(offset).limit(limit).all()

    def list_all_messages(
        self,
        *,
        status: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        search: str | None = None,
        recipient_q: str | None = None,
        info_recipient_only: bool = False,
        only_info_to_test_ii: bool = False,
        only_info_to: bool = False,
    ) -> list[EmailMessageRow]:
        """Все письма по фильтрам (без пагинации) — для Excel-выгрузки."""
        query = self._session.query(EmailMessageRow).order_by(EmailMessageRow.received_at.desc())
        query = self._apply_message_filters(
            query,
            status=status,
            date_from=date_from,
            date_to=date_to,
            search=search,
            recipient_q=recipient_q,
            info_recipient_only=info_recipient_only,
            only_info_to_test_ii=only_info_to_test_ii,
            only_info_to=only_info_to,
        )
        query = self._apply_routing_dedupe(query)
        return query.all()

    def count_erp_created(
        self,
        *,
        date_from: date | None = None,
        date_to: date | None = None,
        search: str | None = None,
        recipient_q: str | None = None,
        info_recipient_only: bool = False,
        only_info_to_test_ii: bool = False,
        only_info_to: bool = False,
    ) -> int:
        query = self._session.query(func.count(EmailMessageRow.id)).filter(
            EmailMessageRow.erp_document_number.is_not(None),
            EmailMessageRow.erp_document_number != "",
            ~EmailMessageRow.erp_document_number.in_(("SKIP-ERP", "DRY-RUN")),
            ~EmailMessageRow.erp_document_number.like(f"{STUB_ERP_DOCUMENT_PREFIX}%"),
            EmailMessageRow.erp_task_id.is_not(None),
            EmailMessageRow.erp_task_id != "",
            ~EmailMessageRow.erp_task_id.like(f"{STUB_ERP_TASK_PREFIX}%"),
        )
        query = self._apply_message_filters(
            query,
            date_from=date_from,
            date_to=date_to,
            search=search,
            recipient_q=recipient_q,
            info_recipient_only=info_recipient_only,
            only_info_to_test_ii=only_info_to_test_ii,
            only_info_to=only_info_to,
        )
        return int(query.scalar() or 0)

    def count_erp_skipped(
        self,
        *,
        date_from: date | None = None,
        date_to: date | None = None,
        search: str | None = None,
        recipient_q: str | None = None,
        info_recipient_only: bool = False,
        only_info_to_test_ii: bool = False,
        only_info_to: bool = False,
    ) -> int:
        query = self._session.query(func.count(EmailMessageRow.id)).filter(
            or_(
                EmailMessageRow.erp_document_number == "SKIP-ERP",
                EmailMessageRow.erp_document_number == "DRY-RUN",
                EmailMessageRow.erp_document_number.like(f"{STUB_ERP_DOCUMENT_PREFIX}%"),
            )
        )
        query = self._apply_message_filters(
            query,
            date_from=date_from,
            date_to=date_to,
            search=search,
            recipient_q=recipient_q,
            info_recipient_only=info_recipient_only,
            only_info_to_test_ii=only_info_to_test_ii,
            only_info_to=only_info_to,
        )
        return int(query.scalar() or 0)

    def count_messages(
        self,
        *,
        status: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        search: str | None = None,
        recipient_q: str | None = None,
        info_recipient_only: bool = False,
        only_info_to_test_ii: bool = False,
        only_info_to: bool = False,
    ) -> int:
        query = self._session.query(EmailMessageRow)
        query = self._apply_message_filters(
            query,
            status=status,
            date_from=date_from,
            date_to=date_to,
            search=search,
            recipient_q=recipient_q,
            info_recipient_only=info_recipient_only,
            only_info_to_test_ii=only_info_to_test_ii,
            only_info_to=only_info_to,
        )
        query = self._apply_routing_dedupe(query)
        return int(query.count())

    def delete_demo_messages(self) -> int:
        """Удаляет демо/тестовые записи из email_messages (вложения — CASCADE)."""
        query = self._session.query(EmailMessageRow).filter(demo_row_filter(EmailMessageRow))
        count = query.count()
        query.delete(synchronize_session=False)
        return count

    def count_by_status(
        self,
        *,
        date_from: date | None = None,
        date_to: date | None = None,
        search: str | None = None,
        recipient_q: str | None = None,
        info_recipient_only: bool = False,
        only_info_to_test_ii: bool = False,
        only_info_to: bool = False,
    ) -> dict[str, int]:
        query = self._session.query(EmailMessageRow)
        query = self._apply_message_filters(
            query,
            date_from=date_from,
            date_to=date_to,
            search=search,
            recipient_q=recipient_q,
            info_recipient_only=info_recipient_only,
            only_info_to_test_ii=only_info_to_test_ii,
            only_info_to=only_info_to,
        )
        query = self._apply_routing_dedupe(query)
        rows = query.with_entities(EmailMessageRow.status, func.count(EmailMessageRow.id)).group_by(
            EmailMessageRow.status
        ).all()
        return {status: int(count) for status, count in rows}

    def count_operator_review_states(
        self,
        *,
        status: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        search: str | None = None,
        recipient_q: str | None = None,
        info_recipient_only: bool = False,
        only_info_to_test_ii: bool = False,
        only_info_to: bool = False,
    ) -> dict[str, int]:
        is_corrected, is_verified, is_pending = operator_review_state_sql_flags(
            EmailMessageRow.raw_payload_json,
            EmailMessageRow.id,
        )
        query = self._session.query(EmailMessageRow)
        query = self._apply_message_filters(
            query,
            status=status,
            date_from=date_from,
            date_to=date_to,
            search=search,
            recipient_q=recipient_q,
            info_recipient_only=info_recipient_only,
            only_info_to_test_ii=only_info_to_test_ii,
            only_info_to=only_info_to,
        )
        query = self._apply_routing_dedupe(query)
        row = query.with_entities(
            func.count(EmailMessageRow.id).label("all"),
            func.sum(case((is_corrected, 1), else_=0)).label("corrected"),
            func.sum(case((is_verified, 1), else_=0)).label("verified"),
            func.sum(case((is_pending, 1), else_=0)).label("pending"),
        ).one()
        return {
            "all": int(row.all or 0),
            "corrected": int(row.corrected or 0),
            "verified": int(row.verified or 0),
            "pending": int(row.pending or 0),
        }

    def batch_operator_review_event_hints(
        self, email_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, dict[str, bool]]:
        """operator_approve / operator_change по email_id для inference operator_review_state."""
        if not email_ids:
            return {}
        from agent_pochta.db.models import ClassificationEventRow

        rows = (
            self._session.query(
                ClassificationEventRow.email_id,
                ClassificationEventRow.event_type,
            )
            .filter(
                ClassificationEventRow.email_id.in_(email_ids),
                ClassificationEventRow.category == "department",
                ClassificationEventRow.event_type.in_(("operator_approve", "operator_change")),
                ClassificationEventRow.actor == "operator",
            )
            .all()
        )
        hints: dict[uuid.UUID, dict[str, bool]] = {}
        for email_id, event_type in rows:
            if email_id is None:
                continue
            entry = hints.setdefault(
                email_id,
                {"has_operator_approve": False, "has_operator_change": False},
            )
            if event_type == "operator_approve":
                entry["has_operator_approve"] = True
            elif event_type == "operator_change":
                entry["has_operator_change"] = True
        return hints

    def touch_processing_lease(self, row: EmailMessageRow) -> None:
        """Продлевает lease обработки — защита от повторной постановки в очередь."""
        payload: dict
        try:
            raw = json.loads(row.raw_payload_json or "{}")
        except json.JSONDecodeError:
            raw = {}
        payload = raw if isinstance(raw, dict) else {}
        payload["processing_started_at"] = datetime.utcnow().isoformat()
        row.raw_payload_json = json.dumps(payload, ensure_ascii=False)

    def ensure_processing_row(self, email: EmailMessage) -> uuid.UUID:
        """Создаёт или обновляет запись со status=processing до завершения графа."""
        row = self.get_by_message_id(email.message_id)
        payload = email_to_task_payload(email, for_storage=True)

        payload["processing_started_at"] = datetime.utcnow().isoformat()
        payload_json = json.dumps(payload, ensure_ascii=False)

        if row is None:
            row = EmailMessageRow(
                id=uuid.uuid4(),
                message_id=email.message_id,
                received_at=email.received_at,
                mailbox=email.mailbox,
                sender_email=email.sender_email,
                sender_name=email.sender_name,
                subject=email.subject,
                attachments_count=len(email.attachments),
                status=ProcessingStatus.PROCESSING.value,
                human_review=False,
                raw_payload_json=payload_json,
            )
            self._session.add(row)
        else:
            row.sender_name = email.sender_name
            row.subject = email.subject
            row.received_at = email.received_at
            row.mailbox = email.mailbox
            row.sender_email = email.sender_email
            row.attachments_count = len(email.attachments)
            row.status = ProcessingStatus.PROCESSING.value
            row.human_review = False
            row.raw_payload_json = payload_json

        self._sync_is_info_recipient(row)
        self._session.flush()
        return row.id

    def upsert_from_state(self, state: AgentState) -> uuid.UUID:
        email = state["email"]
        row = self.get_by_message_id(email.message_id)
        from agent_pochta.stats.classification_log import (
            log_agent_classification_from_state,
            snapshot_from_row,
        )

        before = snapshot_from_row(row)
        if row is None:
            row = EmailMessageRow(
                id=uuid.uuid4(),
                message_id=email.message_id,
                received_at=email.received_at,
                mailbox=email.mailbox,
                sender_email=email.sender_email,
            )
            self._session.add(row)

        row.sender_name = email.sender_name
        row.subject = email.subject
        row.received_at = email.received_at
        row.mailbox = email.mailbox
        row.sender_email = email.sender_email
        row.processed_at = datetime.now(timezone.utc).replace(tzinfo=None)
        row.attachments_count = len(email.attachments)
        row.agent_version = get_settings().agent_version
        payload = email_to_task_payload(email, for_storage=True)
        meta = state.get("meta") or {}
        if xml := meta.get("xml_document"):
            payload["xml_document"] = xml
        if routing_decision := meta.get("routing_decision"):
            payload["routing_decision"] = routing_decision
        if "rag_fallback" in meta:
            payload["rag_fallback"] = bool(meta.get("rag_fallback"))
        if routing_source := meta.get("routing_source"):
            payload["routing_source"] = routing_source
        if meta.get("bge_score") is not None:
            payload["bge_score"] = meta.get("bge_score")
        if bge_dept := meta.get("bge_dept_correct_id"):
            payload["bge_dept_correct_id"] = bge_dept
        if routing_recipient := meta.get("routing_recipient"):
            payload["routing_recipient"] = routing_recipient
        if dialog := meta.get("dialog"):
            payload["dialog"] = dialog
        if erp_attachments := meta.get("erp_attachments"):
            payload["erp_attachments"] = erp_attachments
        if erp_attachment_errors := meta.get("erp_attachment_errors"):
            payload["erp_attachment_errors"] = erp_attachment_errors
        settings = get_settings()
        if combined := state.get("combined_text"):
            payload["embedding_source_text"] = _sanitize_pg_text(
                combined[: settings.email_rag_max_source_chars]
            )
        payload.pop("qdrant_indexed_at", None)
        payload.pop("qdrant_chunk_count", None)
        row.raw_payload_json = json.dumps(payload, ensure_ascii=False)

        spam = state.get("spam")
        if spam is not None:
            row.is_spam = spam.is_spam
            row.spam_confidence = spam.confidence
            row.spam_reason = spam.reason

        sender = state.get("sender")
        if sender is not None:
            row.is_new_contractor = sender.is_new_contractor
            if sender.contractor is not None:
                row.contractor_id = sender.contractor.contractor_id

        routing = state.get("routing")
        if routing is not None:
            from agent_pochta.services.routing_departments import resolve_department_display_name

            row.department_id = routing.department_id
            row.department_name = resolve_department_display_name(
                routing.department_id,
                routing.department_name,
            )
            row.dept_confidence = routing.confidence
            row.priority = routing.priority.value

        if summary := state.get("summary_ru"):
            row.summary_ru = _sanitize_pg_text(summary)

        erp = state.get("erp")
        if erp is not None:
            if erp.erp_document_number:
                row.erp_document_number = erp.erp_document_number
            if erp.erp_task_id:
                row.erp_task_id = erp.erp_task_id
            if erp.success:
                row.erp_retry_count = 0

        status = state.get("status", ProcessingStatus.PROCESSING)
        if status == ProcessingStatus.PROCESSING:
            status = ProcessingStatus.DONE
        row.status = status.value if isinstance(status, ProcessingStatus) else str(status)
        row.human_review = bool(state.get("human_review"))

        if escalation := state.get("escalation_reason"):
            if is_routing_escalation_reason(escalation):
                payload["hitl_reason"] = escalation
                row.raw_payload_json = json.dumps(payload, ensure_ascii=False)
            elif not row.spam_reason:
                row.spam_reason = escalation

        self._sync_is_info_recipient(row)

        row.attachments.clear()
        settings = get_settings()
        for attachment in email.attachments:
            stored_text = _sanitize_pg_text(attachment.extracted_text)
            if stored_text:
                stored_text = stored_text[: settings.document_storage_excerpt_chars]
            row.attachments.append(
                EmailAttachmentRow(
                    id=uuid.uuid4(),
                    filename=attachment.filename,
                    mime_type=attachment.mime_type,
                    size_bytes=attachment.size_bytes,
                    extracted_text=stored_text,
                    ocr_used=attachment.ocr_used,
                )
            )

        self._session.flush()
        log_agent_classification_from_state(self._session, row, before, state)
        return row.id

    def mark_restored_from_spam(self, row_id: uuid.UUID) -> EmailMessageRow | None:
        row = self.get_by_id(row_id)
        if row is None:
            return None
        row.status = ProcessingStatus.AWAITING_HUMAN.value
        row.is_spam = False
        row.spam_reason = "Восстановлено из спама: требуется подтверждение оператора"
        row.spam_confidence = None
        row.human_review = True
        row.processed_at = None
        self.clear_xml_document(row)
        self._session.flush()
        return row

    def apply_human_resolution(
        self,
        row_id: uuid.UUID,
        *,
        status: str,
        department_id: str | None = None,
        department_name: str | None = None,
        is_spam: bool | None = None,
        contractor_id: str | None = None,
        partner_name: str | None = None,
    ) -> EmailMessageRow | None:
        row = self.get_by_id(row_id)
        if row is None:
            return None
        row.status = status
        row.human_review = False
        if department_id is not None:
            row.department_id = department_id
        if department_name is not None:
            row.department_name = department_name
        if is_spam is not None:
            row.is_spam = is_spam
        from agent_pochta.services.contractor_seed import contractor_id_from_email, is_valid_sender_email
        from agent_pochta.services.llm_analyze import normalize_partner_name

        normalized_partner = normalize_partner_name(partner_name)
        if contractor_id is not None:
            row.contractor_id = contractor_id
            row.is_new_contractor = False
        elif normalized_partner:
            row.is_new_contractor = True
            if is_valid_sender_email(row.sender_email):
                row.contractor_id = contractor_id_from_email(row.sender_email)
        self._session.flush()
        return row

    @staticmethod
    def _sync_is_info_recipient(row: EmailMessageRow) -> None:
        row.is_info_recipient = compute_is_info_recipient(
            mailbox=row.mailbox,
            raw_payload_json=row.raw_payload_json,
        )

    @staticmethod
    def _payload_dict(row: EmailMessageRow) -> dict:
        if not row.raw_payload_json:
            return {}
        try:
            payload = json.loads(row.raw_payload_json)
        except json.JSONDecodeError:
            return {}
        return payload if isinstance(payload, dict) else {}

    def clear_xml_document(self, row: EmailMessageRow) -> None:
        payload = self._payload_dict(row)
        if "xml_document" not in payload:
            return
        payload.pop("xml_document", None)
        row.raw_payload_json = json.dumps(payload, ensure_ascii=False)

    def set_xml_document(self, row: EmailMessageRow, xml: str) -> None:
        payload = self._payload_dict(row)
        payload["xml_document"] = xml
        row.raw_payload_json = json.dumps(payload, ensure_ascii=False)

    def set_operator_verified(self, row: EmailMessageRow, verified: bool = True) -> None:
        """Флаг «Проверено оператором» в raw_payload_json (done/error)."""
        from datetime import datetime, timezone

        payload = self._payload_dict(row)
        if verified:
            payload["operator_verified"] = True
            payload["operator_verified_at"] = (
                datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
            )
        else:
            payload.pop("operator_verified", None)
            payload.pop("operator_verified_at", None)
        row.raw_payload_json = json.dumps(payload, ensure_ascii=False)

    def set_operator_corrected(self, row: EmailMessageRow, corrected: bool) -> None:
        """Флаг «оператор вносил правки» для табличного вида и статистики."""
        payload = self._payload_dict(row)
        if corrected:
            payload["operator_corrected"] = True
        else:
            payload["operator_corrected"] = False
        row.raw_payload_json = json.dumps(payload, ensure_ascii=False)

    def rebuild_xml_after_human_correction(
        self,
        row: EmailMessageRow,
        email: EmailMessage,
        *,
        original_department_id: str | None = None,
        original_department_name: str | None = None,
        partner_override: str | None = None,
        process_override: str | None = None,
        organization_override: str | None = None,
    ) -> str | None:
        from agent_pochta.routing.xml_parser import parse_document_xml, rebuild_xml_document_from_row
        from agent_pochta.stats.change_log import log_xml_field_changes

        payload = self._payload_dict(row)
        existing = parse_document_xml(str(payload.get("xml_document") or ""))

        xml = rebuild_xml_document_from_row(
            row,
            email,
            original_department_id=original_department_id,
            original_department_name=original_department_name,
            partner_override=partner_override,
            process_override=process_override,
            organization_override=organization_override,
        )
        if xml:
            self.set_xml_document(row, xml)
            log_xml_field_changes(
                self._session,
                message_id=row.message_id,
                email_id=row.id,
                existing=existing,
                organization=organization_override,
                partner=partner_override,
                process=process_override,
            )
        return xml

    def increment_erp_retry(self, row_id: uuid.UUID) -> int:
        row = self.get_by_id(row_id)
        if row is None:
            return 0
        row.erp_retry_count += 1
        self._session.flush()
        return row.erp_retry_count

    def cache_fetched_body(
        self,
        row_id: uuid.UUID,
        *,
        body_text: str,
        body_html: str | None = None,
    ) -> EmailMessageRow | None:
        """Кеширует загруженное из IMAP тело в raw_payload_json (on-demand для UI)."""
        row = self.get_by_id(row_id)
        if row is None:
            return None
        try:
            payload = json.loads(row.raw_payload_json or "{}")
        except json.JSONDecodeError:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        payload["body_text"] = body_text
        if body_html:
            payload["body_html"] = body_html
        else:
            payload.pop("body_html", None)
        payload["body_fetched_at"] = datetime.now(timezone.utc).replace(tzinfo=None).isoformat() + "Z"
        row.raw_payload_json = json.dumps(payload, ensure_ascii=False)
        self._session.flush()
        return row

    def load_email_from_row(self, row: EmailMessageRow) -> EmailMessage | None:
        """Восстанавливает EmailMessage из БД (без бинарного содержимого вложений).

        Извлечённый текст вложений подставляется из email_attachments / text_excerpt.
        """
        if not row.raw_payload_json:
            return None
        from agent_pochta.email_payload import email_from_task_payload

        email = email_from_task_payload(json.loads(row.raw_payload_json))
        stored_by_name = {a.filename: a for a in row.attachments if a.filename}
        payload_attachments = {
            item.get("filename"): item
            for item in (json.loads(row.raw_payload_json).get("attachments") or [])
            if isinstance(item, dict) and item.get("filename")
        }
        for att in email.attachments:
            db_att = stored_by_name.get(att.filename)
            if db_att and db_att.extracted_text and not att.extracted_text:
                att.extracted_text = db_att.extracted_text
                att.ocr_used = bool(db_att.ocr_used)
            elif not att.extracted_text:
                meta = payload_attachments.get(att.filename) or {}
                excerpt = meta.get("text_excerpt")
                if excerpt:
                    att.extracted_text = str(excerpt)
                    att.ocr_used = bool(meta.get("ocr_used"))
        return email

    @staticmethod
    def learning_text_from_row(row: EmailMessageRow, email: EmailMessage) -> str:
        """Текст для обучения RAG/спам: body если есть (legacy), иначе summary или subject."""
        body = (email.body_text or "").strip()
        if body:
            return body
        if row.summary_ru:
            return row.summary_ru.strip()
        return (email.subject or "").strip()

    def build_routing_from_row(self, row: EmailMessageRow) -> RoutingResult | None:
        if not row.department_id:
            return None
        try:
            priority = Priority(row.priority)
        except ValueError:
            priority = Priority.NORMAL
        return RoutingResult(
            department_id=row.department_id,
            department_name=row.department_name or row.department_id,
            confidence=row.dept_confidence or 0.0,
            reasoning="Восстановлено из записи БД",
            priority=priority,
        )


def persist_processing_start(email: EmailMessage) -> uuid.UUID | None:
    """Фиксирует начало обработки в БД (вкладка «В работе» в UI)."""
    from agent_pochta.demo_filter import is_demo_email

    if is_demo_email(email):
        return None

    factory = get_session_factory()
    try:
        with factory() as session:
            repo = EmailRepository(session)
            row_id = repo.ensure_processing_row(email)
            session.commit()
            return row_id
    except Exception:
        return None


def persist_processing_result(state: AgentState) -> uuid.UUID | None:
    from agent_pochta.demo_filter import is_demo_email

    email = state.get("email")
    if email is not None and is_demo_email(email):
        return None

    factory = get_session_factory()
    try:
        with factory() as session:
            repo = EmailRepository(session)
            row_id = repo.upsert_from_state(state)
            session.commit()
            return row_id
    except Exception:
        logger.exception("persist_processing_result_failed", message_id=getattr(state.get("email"), "message_id", None))
        return None


def with_repository(fn):
    """Helper for API handlers."""

    def wrapper(*args, **kwargs):
        factory = get_session_factory()
        with factory() as session:
            repo = EmailRepository(session)
            result = fn(repo, *args, **kwargs)
            session.commit()
            return result

    return wrapper
