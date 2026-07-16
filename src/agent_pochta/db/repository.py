"""Сохранение и чтение результатов обработки письма в PostgreSQL (раздел 7 ТЗ)."""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timezone

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from agent_pochta.db.message_filters import msk_day_end_exclusive_utc, msk_day_start_utc
from agent_pochta.demo_filter import demo_row_filter

from agent_pochta.config import get_settings
from agent_pochta.db.models import EmailAttachmentRow, EmailMessageRow
from agent_pochta.db.session import get_session_factory
from agent_pochta.email_payload import email_to_task_payload
from agent_pochta.schemas import EmailMessage, Priority, ProcessingStatus, RoutingResult
from agent_pochta.state import AgentState


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
        mailboxes: list[str] | None = None,
    ):
        if mailboxes:
            query = query.filter(EmailMessageRow.mailbox.in_(mailboxes))
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
        query = query.filter(~demo_row_filter(EmailMessageRow))
        return query

    def list_messages(
        self,
        *,
        status: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        search: str | None = None,
        mailboxes: list[str] | None = None,
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
            mailboxes=mailboxes,
        )
        return query.offset(offset).limit(limit).all()

    def count_messages(
        self,
        *,
        status: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        search: str | None = None,
        mailboxes: list[str] | None = None,
    ) -> int:
        query = self._session.query(func.count(EmailMessageRow.id))
        query = self._apply_message_filters(
            query,
            status=status,
            date_from=date_from,
            date_to=date_to,
            search=search,
            mailboxes=mailboxes,
        )
        return int(query.scalar() or 0)

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
        mailboxes: list[str] | None = None,
    ) -> dict[str, int]:
        query = self._session.query(EmailMessageRow.status, func.count(EmailMessageRow.id))
        query = self._apply_message_filters(
            query,
            date_from=date_from,
            date_to=date_to,
            search=search,
            mailboxes=mailboxes,
        )
        rows = query.group_by(EmailMessageRow.status).all()
        return {status: int(count) for status, count in rows}

    def upsert_from_state(self, state: AgentState) -> uuid.UUID:
        email = state["email"]
        row = self.get_by_message_id(email.message_id)
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
        if xml := (state.get("meta") or {}).get("xml_document"):
            payload["xml_document"] = xml
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
            row.department_id = routing.department_id
            row.department_name = routing.department_name
            row.dept_confidence = routing.confidence
            row.priority = routing.priority.value

        if summary := state.get("summary_ru"):
            row.summary_ru = summary

        erp = state.get("erp")
        if erp is not None and erp.success:
            row.erp_document_number = erp.erp_document_number
            row.erp_task_id = erp.erp_task_id
            row.erp_retry_count = 0

        status = state.get("status", ProcessingStatus.PROCESSING)
        if status == ProcessingStatus.PROCESSING:
            status = ProcessingStatus.DONE
        row.status = status.value if isinstance(status, ProcessingStatus) else str(status)
        row.human_review = bool(state.get("human_review"))

        if escalation := state.get("escalation_reason"):
            if not row.spam_reason:
                row.spam_reason = escalation

        row.attachments.clear()
        settings = get_settings()
        for attachment in email.attachments:
            stored_text = attachment.extracted_text
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
