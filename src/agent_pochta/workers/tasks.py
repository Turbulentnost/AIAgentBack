"""Celery-задачи: обработка письма, IMAP poll, retry 1С."""

from __future__ import annotations

import json
import uuid

import structlog

from agent_pochta.config import get_settings
from agent_pochta.db.models import EmailMessageRow
from agent_pochta.db.repository import EmailRepository, persist_processing_start
from agent_pochta.db.session import get_session_factory
from agent_pochta.demo_filter import is_demo_email, is_demo_message
from agent_pochta.email_payload import email_from_task_payload, email_to_task_payload
from agent_pochta.routing.recipients import (
    normalize_routing_email,
    routing_message_id,
    split_routing_recipients,
)
from agent_pochta.schemas import EmailMessage, ProcessingStatus
from agent_pochta.routing.learning import learn_from_not_spam
from agent_pochta.workers.celery_app import celery_app
from agent_pochta.workers.runtime import get_worker_graph

logger = structlog.get_logger(__name__)

_SKIP_IMAP_REQUEUE_STATUSES = {
    ProcessingStatus.DONE.value,
    ProcessingStatus.SPAM.value,
    ProcessingStatus.AWAITING_HUMAN.value,
    ProcessingStatus.ERROR.value,
    ProcessingStatus.DIALOG.value,
}

# Lease for in-flight rows: stale PROCESSING (worker crash) may be re-enqueued.
_STALE_PROCESSING_AFTER_SEC = 900


def _processing_started_at(row: EmailMessageRow):
    """UTC-naive datetime when processing lease was taken, or None for legacy orphans."""
    from datetime import datetime

    if not row.raw_payload_json:
        return None
    try:
        payload = json.loads(row.raw_payload_json)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    raw = payload.get("processing_started_at")
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def _is_terminal_in_db(message_id: str) -> bool:
    """True, если запись уже в финальном статусе (повторный прогон не нужен)."""
    factory = get_session_factory()
    try:
        with factory() as session:
            row = session.query(EmailMessageRow).filter_by(message_id=message_id).one_or_none()
    except Exception:
        return False
    if row is None:
        return False
    return row.status in _SKIP_IMAP_REQUEUE_STATUSES


def _should_skip_imap_enqueue(message_id: str) -> bool:
    """True, если IMAP/catchup не должен снова ставить письмо в очередь."""
    from datetime import datetime, timedelta

    if _is_terminal_in_db(message_id):
        return True

    factory = get_session_factory()
    try:
        with factory() as session:
            row = session.query(EmailMessageRow).filter_by(message_id=message_id).one_or_none()
    except Exception:
        return False
    if row is None or row.status != ProcessingStatus.PROCESSING.value:
        return False

    started = _processing_started_at(row)
    if started is None:
        return False
    age = datetime.utcnow() - started
    return age < timedelta(seconds=_STALE_PROCESSING_AFTER_SEC)


def _is_already_processed(message_id: str) -> bool:
    return _should_skip_imap_enqueue(message_id)



def should_enqueue_email(email: EmailMessage) -> bool:
    """False, если все получатели письма уже в терминальном статусе в БД."""
    recipients = split_routing_recipients(email)
    for recipient in recipients:
        attempt_id = routing_message_id(email.message_id, recipient)
        if not _should_skip_imap_enqueue(attempt_id):
            return True
    return False


def recover_stale_processing(*, limit: int | None = None, force: bool = False) -> dict:
    """Повторно ставит в очередь записи status=processing, зависшие дольше lease."""
    from datetime import datetime, timedelta

    from sqlalchemy import func

    settings = get_settings()
    if limit is None:
        limit = settings.stale_recovery_limit

    factory = get_session_factory()
    recovered = 0
    skipped_fresh = 0
    skipped_no_payload = 0
    deleted_orphans = 0
    deleted_demo = 0
    errors: list[str] = []
    to_enqueue: list[tuple[str, dict]] = []

    with factory() as session:
        processing_count = (
            session.query(func.count(EmailMessageRow.id))
            .filter(EmailMessageRow.status == ProcessingStatus.PROCESSING.value)
            .scalar()
            or 0
        )
        if not force and processing_count >= settings.processing_backlog_pause_threshold:
            return {
                "recovered": 0,
                "deleted_orphans": 0,
                "deleted_demo": 0,
                "skipped_fresh": 0,
                "skipped_no_payload": 0,
                "skipped_backlog": processing_count,
                "errors": [],
            }

        repo = EmailRepository(session)
        rows = (
            session.query(EmailMessageRow)
            .filter(EmailMessageRow.status == ProcessingStatus.PROCESSING.value)
            .order_by(EmailMessageRow.received_at.asc())
            .limit(max(limit * 3, 100))
            .all()
        )
        for row in rows:
            if recovered >= limit:
                break
            if is_demo_message(message_id=row.message_id, sender_email=row.sender_email or ""):
                session.delete(row)
                deleted_demo += 1
                continue
            if not force:
                started = _processing_started_at(row)
                if started is not None:
                    age = datetime.utcnow() - started
                    if age < timedelta(seconds=_STALE_PROCESSING_AFTER_SEC):
                        skipped_fresh += 1
                        continue

            email = repo.load_email_from_row(row)
            if email is None:
                skipped_no_payload += 1
                continue
            if is_demo_email(email):
                session.delete(row)
                deleted_demo += 1
                continue

            email = normalize_routing_email(email)
            canonical_id = routing_message_id(
                email.message_id,
                email.routing_recipient or split_routing_recipients(email)[0],
            )
            if row.message_id != canonical_id:
                canonical = repo.get_by_message_id(canonical_id)
                if canonical is not None and canonical.id != row.id:
                    session.delete(row)
                    deleted_orphans += 1
                    continue
                row.message_id = canonical_id

            repo.touch_processing_lease(row)
            to_enqueue.append((canonical_id, email_to_task_payload(email)))
            recovered += 1
        session.commit()

    for message_id, payload in to_enqueue:
        try:
            process_email_task.delay(payload)
        except Exception as exc:
            errors.append(f"{message_id}: {exc}")

    if recovered or deleted_orphans or deleted_demo:
        logger.info(
            "stale_processing_recovered",
            recovered=recovered,
            deleted_orphans=deleted_orphans,
            deleted_demo=deleted_demo,
            skipped_fresh=skipped_fresh,
            skipped_no_payload=skipped_no_payload,
            force=force,
        )

    return {
        "recovered": recovered,
        "deleted_orphans": deleted_orphans,
        "deleted_demo": deleted_demo,
        "skipped_fresh": skipped_fresh,
        "skipped_no_payload": skipped_no_payload,
        "errors": errors,
    }


def extract_xml_document_from_row(row: EmailMessageRow) -> str | None:
    """Извлекает xml_document из raw_payload_json строки БД."""
    if not row.raw_payload_json:
        return None
    try:
        payload = json.loads(row.raw_payload_json)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    xml = payload.get("xml_document")
    if isinstance(xml, str) and xml.strip():
        return xml.strip()
    return None


def _merge_attachment_meta_into_payload(raw_payload_json: str | None, attachment_meta: dict) -> str | None:
    if not attachment_meta or not raw_payload_json:
        return raw_payload_json
    from agent_pochta.services.erp_sync import merge_erp_sync_meta_into_payload

    return merge_erp_sync_meta_into_payload(raw_payload_json, attachment_meta)


def _persist_erp_sync_result(message_id: str, sync_result: dict) -> None:
    sync_meta = sync_result.get("erp_sync_meta")
    if not sync_meta:
        return
    factory = get_session_factory()
    with factory() as session:
        db_row = EmailRepository(session).get_by_message_id(message_id)
        if db_row is None:
            return
        if cleared_payload := sync_result.get("raw_payload_json"):
            db_row.raw_payload_json = cleared_payload
        merged = _merge_attachment_meta_into_payload(db_row.raw_payload_json, sync_meta)
        if merged is not None:
            db_row.raw_payload_json = merged
        db_row.human_review = False
        session.commit()


def _sync_existing_erp_document(
    *,
    message_id: str,
    row: EmailMessageRow,
    email: EmailMessage,
    routing,
    summary_ru: str,
    container,
    force_reattach_filenames: set[str] | None = None,
) -> dict:
    """PATCH полей + догрузка вложений к уже созданному документу 1С."""
    from agent_pochta.services.erp_sync import sync_existing_erp_document

    xml_document = extract_xml_document_from_row(row)
    result = sync_existing_erp_document(
        message_id=message_id,
        row=row,
        email=email,
        routing=routing,
        summary_ru=summary_ru or "",
        integration=container.integration,
        vault=container.vault,
        xml_document=xml_document,
        force_reattach_filenames=force_reattach_filenames,
    )
    _persist_erp_sync_result(message_id, result)
    return result


def _attach_files_to_existing_erp_document(
    *,
    message_id: str,
    row: EmailMessageRow,
    email: EmailMessage,
    container,
    routing=None,
    summary_ru: str = "",
) -> dict:
    """Обратная совместимость: attach-only через sync_existing."""
    from agent_pochta.db.repository import EmailRepository

    if routing is None:
        factory = get_session_factory()
        with factory() as session:
            routing = EmailRepository(session).build_routing_from_row(row)
    if routing is None:
        return {"ok": False, "reason": "no_routing"}

    return _sync_existing_erp_document(
        message_id=message_id,
        row=row,
        email=email,
        routing=routing,
        summary_ru=summary_ru or (row.summary_ru or ""),
        container=container,
    )


def _resolve_retry_erp_mode(row: EmailMessageRow, email: EmailMessage) -> str:
    """create | sync_existing."""
    from agent_pochta.services.erp_attachments import (
        existing_erp_document_ref_key,
    )

    doc_ref = existing_erp_document_ref_key(row)
    doc_number = (row.erp_document_number or "").strip()
    if not doc_ref or not doc_number or doc_number in {"SKIP-ERP", "DRY-RUN"}:
        return "create"
    return "sync_existing"


def _schedule_erp_retry(message_id: str) -> None:
    settings = get_settings()
    retry_erp_task.apply_async(
        args=[message_id],
        countdown=settings.erp_retry_delay_sec,
        queue="erp",
    )


@celery_app.task(name="agent_pochta.process_email", bind=True, max_retries=3, default_retry_delay=60)
def process_email_task(self, email_payload: dict) -> dict:
    email = normalize_routing_email(email_from_task_payload(email_payload))
    if is_demo_email(email):
        logger.info("skip_demo_email", message_id=email.message_id, sender=email.sender_email)
        return {
            "skipped": True,
            "message_id": email.message_id,
            "reason": "demo_message",
        }

    recipients = split_routing_recipients(email)
    results: list[dict] = []

    for recipient in recipients:
        attempt_email = email.model_copy(update={"routing_recipient": recipient})
        attempt_id = routing_message_id(email.message_id, recipient)
        attempt_email = attempt_email.model_copy(update={"message_id": attempt_id})

        if _is_terminal_in_db(attempt_id):
            results.append(
                {"skipped": True, "message_id": attempt_id, "recipient": recipient, "reason": "already_processed"}
            )
            continue

        row_id = persist_processing_start(attempt_email)
        graph = get_worker_graph()
        try:
            result = graph.invoke({"email": attempt_email})
        except Exception as exc:
            logger.exception("process_email_failed", message_id=attempt_id)
            if row_id is not None and self.request.retries >= self.max_retries:
                _fail_reprocessing(row_id, reason=f"Сбой обработки письма: {exc}")
            raise

        status = result.get("status")
        erp = result.get("erp")
        if status == ProcessingStatus.ERROR and erp and not erp.success:
            meta = result.get("meta") or {}
            if meta.get("erp_retry_scheduled"):
                _schedule_erp_retry(attempt_id)

        results.append(
            {
                "skipped": False,
                "message_id": attempt_id,
                "recipient": recipient,
                "status": str(status),
                "db_record_id": (result.get("meta") or {}).get("db_record_id"),
                "trace": result.get("trace", []),
            }
        )

    if len(results) == 1:
        return results[0]
    return {"multi_recipient": True, "results": results}


def _celery_queue_depth(queue_name: str = "celery") -> int:
    """Passive RabbitMQ declare — сколько задач ждёт в очереди."""
    try:
        with celery_app.connection_or_acquire() as conn:
            channel = conn.default_channel
            declared = channel.queue_declare(queue=queue_name, passive=True)
            return int(getattr(declared, "message_count", 0) or 0)
    except Exception:
        return 0


@celery_app.task(
    name="agent_pochta.poll_imap",
    queue="imap",
    # IMAP catch-up on large mailboxes (info@) can exceed the global 600s soft limit.
    soft_time_limit=900,
    time_limit=1080,
)
def poll_imap_task() -> dict:
    from agent_pochta.imap.poller import poll_mailboxes

    imap_result = poll_mailboxes()
    queue_depth = _celery_queue_depth()
    if queue_depth > 150:
        recovery = {
            "recovered": 0,
            "deleted_orphans": 0,
            "deleted_demo": 0,
            "skipped_fresh": 0,
            "skipped_no_payload": 0,
            "skipped_queue_backlog": queue_depth,
            "errors": [],
        }
    else:
        recovery = recover_stale_processing()
    return {**imap_result, "stale_recovery": recovery}


@celery_app.task(name="agent_pochta.recover_stale_processing")
def recover_stale_processing_task(*, limit: int = 30) -> dict:
    return recover_stale_processing(limit=limit)


@celery_app.task(
    name="agent_pochta.retry_erp",
    bind=True,
    queue="erp",
    # Не дать волне ERP 403/500 занять пул process_email (отдельная очередь erp).
    rate_limit="6/m",
    # IMAP fetch (до 120 с) + OData POST вложений — больше стандартного soft limit.
    soft_time_limit=240,
    time_limit=300,
)
def retry_erp_task(self, message_id: str, *, force_reattach_eml: bool = False) -> dict:
    """Повтор создания документа в 1С (ТЗ §5.2: 10 мин, max 5 попыток)."""
    settings = get_settings()
    from agent_pochta.workers.runtime import get_worker_container

    factory = get_session_factory()
    container = get_worker_container()

    with factory() as session:
        repo = EmailRepository(session)
        row = repo.get_by_message_id(message_id)
        if row is None:
            return {"ok": False, "reason": "not_found"}

        if row.erp_retry_count >= settings.erp_retry_max:
            row.status = ProcessingStatus.ERROR.value
            row.human_review = True
            session.commit()
            return {"ok": False, "reason": "max_retries_exceeded", "attempts": row.erp_retry_count}

        email = repo.load_email_from_row(row)
        routing = repo.build_routing_from_row(row)
        summary_ru = row.summary_ru
        if email is None or routing is None:
            return {"ok": False, "reason": "incomplete_record"}

        from agent_pochta.db.message_filters import email_eligible_for_erp, is_dialog_message, load_payload_dict

        payload = load_payload_dict(row.raw_payload_json)
        if not email_eligible_for_erp(
            mailbox=row.mailbox or email.mailbox,
            to=email.to,
            cc=email.cc,
            routing_recipient=email.routing_recipient,
            payload=payload,
            status=row.status or "",
        ):
            reason = (
                "dialog"
                if is_dialog_message(status=str(row.status or ""), payload=payload)
                else "not_info_mailbox"
            )
            return {"ok": True, "skipped": True, "reason": reason}

        if not summary_ru:
            from agent_pochta.workers.hitl import continue_after_human_approval

            session.commit()
            result = continue_after_human_approval(
                email=email,
                routing=routing,
                container=container,
            )
            erp = result.get("erp")
            return {
                "ok": bool(erp and erp.success),
                "reason": None if erp and erp.success else "summary_or_erp_failed",
                "summary_ru": result.get("summary_ru"),
                "erp_document_number": erp.erp_document_number if erp else None,
                "recovered_from_hitl_gap": True,
            }

        retry_mode = _resolve_retry_erp_mode(row, email)
        if retry_mode == "sync_existing":
            force_reattach: set[str] | None = None
            if force_reattach_eml:
                from agent_pochta.services.erp_attachments import erp_email_upload_marker_names

                force_reattach = erp_email_upload_marker_names(row.erp_document_number)
            session.commit()
            return _sync_existing_erp_document(
                message_id=message_id,
                row=row,
                email=email,
                routing=routing,
                summary_ru=summary_ru or "",
                container=container,
                force_reattach_filenames=force_reattach,
            )

        xml_document = extract_xml_document_from_row(row)
        if not xml_document:
            row.status = ProcessingStatus.ERROR.value
            row.human_review = True
            session.commit()
            return {"ok": False, "reason": "missing_xml_document"}

        attempt = repo.increment_erp_retry(row.id)
        session.commit()

    try:
        res = container.integration.create_incoming_correspondence(
            email, routing, summary_ru, xml_document=xml_document
        )
        doc_id = res.get("erp_document_id")
        attachment_meta: dict = {}
        attach_error: str | None = None
        if doc_id and email is not None:
            from agent_pochta.services.erp_attachments import attach_email_files_to_document

            try:
                attached = attach_email_files_to_document(
                    container.integration,
                    document_ref_key=str(doc_id),
                    email=email,
                    vault=container.vault,
                    erp_document_number=res.get("erp_document_number"),
                )
                if attached:
                    attachment_meta["erp_attachments"] = attached
                else:
                    attach_error = "Не удалось прикрепить письмо к документу 1С (нет файлов для загрузки)"
            except Exception as attach_exc:
                attach_error = str(attach_exc)
                attachment_meta["erp_attachment_errors"] = [attach_error]

        if attach_error:
            retry_row = None
            with factory() as session:
                retry_row = EmailRepository(session).get_by_message_id(message_id)
                if retry_row:
                    retry_row.erp_document_number = res["erp_document_number"]
                    retry_row.erp_task_id = res.get("erp_task_id") or res.get("erp_document_id")
                    if attachment_meta and retry_row.raw_payload_json:
                        merged = _merge_attachment_meta_into_payload(
                            retry_row.raw_payload_json,
                            attachment_meta,
                        )
                        if merged is not None:
                            retry_row.raw_payload_json = merged
                    session.commit()
            if retry_row and retry_row.erp_retry_count < settings.erp_retry_max:
                retry_erp_task.apply_async(
                    args=[message_id],
                    countdown=settings.erp_retry_delay_sec,
                    queue="erp",
                )
            return {
                "ok": False,
                "attempt": attempt,
                "reason": "attach_failed",
                "error": attach_error,
                "erp_document_number": res["erp_document_number"],
                **attachment_meta,
            }

        with factory() as session:
            row = EmailRepository(session).get_by_message_id(message_id)
            if row:
                row.erp_document_number = res["erp_document_number"]
                row.erp_task_id = res.get("erp_task_id") or res.get("erp_document_id")
                row.status = ProcessingStatus.DONE.value
                row.human_review = False
                row.erp_retry_count = 0
                if attachment_meta and row.raw_payload_json:
                    merged = _merge_attachment_meta_into_payload(row.raw_payload_json, attachment_meta)
                    if merged is not None:
                        row.raw_payload_json = merged
                session.commit()
        return {
            "ok": True,
            "attempt": attempt,
            "erp_document_number": res["erp_document_number"],
            **attachment_meta,
        }
    except Exception as exc:
        logger.exception("erp_retry_failed", message_id=message_id, attempt=attempt)
        with factory() as session:
            row = EmailRepository(session).get_by_message_id(message_id)
            if row and row.erp_retry_count < settings.erp_retry_max:
                session.commit()
                retry_erp_task.apply_async(
                    args=[message_id],
                    countdown=settings.erp_retry_delay_sec,
                    queue="erp",
                )
            elif row:
                row.status = ProcessingStatus.ERROR.value
                row.human_review = True
                session.commit()
        return {"ok": False, "attempt": attempt, "error": str(exc)}


@celery_app.task(
    name="agent_pochta.sync_erp_correction",
    queue="erp",
    soft_time_limit=240,
    time_limit=300,
)
def sync_erp_correction_task(message_id: str) -> dict:
    """PATCH документа 1С и догрузка вложений после коррекции оператора."""
    from agent_pochta.workers.runtime import get_worker_container

    factory = get_session_factory()
    container = get_worker_container()

    with factory() as session:
        repo = EmailRepository(session)
        row = repo.get_by_message_id(message_id)
        if row is None:
            return {"ok": False, "reason": "not_found"}

        email = repo.load_email_from_row(row)
        routing = repo.build_routing_from_row(row)
        summary_ru = row.summary_ru
        if email is None or routing is None:
            return {"ok": False, "reason": "incomplete_record"}

        from agent_pochta.db.message_filters import email_eligible_for_erp, is_dialog_message, load_payload_dict

        payload = load_payload_dict(row.raw_payload_json)
        if not email_eligible_for_erp(
            mailbox=row.mailbox or email.mailbox,
            to=email.to,
            cc=email.cc,
            routing_recipient=email.routing_recipient,
            payload=payload,
            status=row.status or "",
        ):
            reason = (
                "dialog"
                if is_dialog_message(status=str(row.status or ""), payload=payload)
                else "not_info_mailbox"
            )
            return {"ok": True, "skipped": True, "reason": reason}

        session.commit()

    return _sync_existing_erp_document(
        message_id=message_id,
        row=row,
        email=email,
        routing=routing,
        summary_ru=summary_ru or "",
        container=container,
    )


def _hitl_meta_from_row(row: EmailMessageRow) -> dict:
    from agent_pochta.db.message_filters import load_payload_dict

    meta: dict = {}
    xml = extract_xml_document_from_row(row)
    if xml:
        meta["xml_document"] = xml
    payload = load_payload_dict(row.raw_payload_json)
    if payload:
        if dialog := payload.get("dialog"):
            meta["dialog"] = dialog
        if routing_decision := payload.get("routing_decision"):
            meta["routing_decision"] = routing_decision
    return meta


def _fail_post_hitl_processing(row_id: uuid.UUID, *, reason: str) -> None:
    """Фиксирует сбой post-HITL без отмены решения оператора (status=error)."""
    factory = get_session_factory()
    with factory() as session:
        row = EmailRepository(session).get_by_id(row_id)
        if row is None or row.status != ProcessingStatus.PROCESSING.value:
            return
        row.status = ProcessingStatus.ERROR.value
        row.human_review = False
        row.spam_reason = reason
        session.commit()


def _resolve_terminal_status(status) -> ProcessingStatus:
    if isinstance(status, ProcessingStatus):
        resolved = status
    else:
        try:
            resolved = ProcessingStatus(str(status))
        except ValueError:
            resolved = ProcessingStatus.DONE
    if resolved == ProcessingStatus.PROCESSING:
        return ProcessingStatus.DONE
    return resolved


def _sync_post_hitl_result(row_id: uuid.UUID, result: dict) -> None:
    """Гарантирует финальный статус в БД после post-HITL пайплайна."""
    from datetime import datetime, timezone

    factory = get_session_factory()
    with factory() as session:
        row = EmailRepository(session).get_by_id(row_id)
        if row is None:
            return

        status = _resolve_terminal_status(result.get("status", ProcessingStatus.DONE))
        row.status = status.value
        row.human_review = bool(result.get("human_review"))

        if summary := result.get("summary_ru"):
            row.summary_ru = summary

        erp = result.get("erp")
        if erp is not None and erp.success:
            row.erp_document_number = erp.erp_document_number
            row.erp_task_id = erp.erp_task_id
            row.erp_retry_count = 0

        if status in {ProcessingStatus.DONE, ProcessingStatus.ERROR}:
            row.processed_at = datetime.now(timezone.utc).replace(tzinfo=None)

        if escalation := result.get("escalation_reason"):
            if not row.spam_reason:
                row.spam_reason = escalation

        session.commit()


def _fail_reprocessing(row_id: uuid.UUID, *, reason: str) -> None:
    """Фиксирует сбой повторной обработки (status=error, требует внимания оператора)."""
    factory = get_session_factory()
    with factory() as session:
        row = EmailRepository(session).get_by_id(row_id)
        if row is None or row.status != ProcessingStatus.PROCESSING.value:
            return
        row.status = ProcessingStatus.ERROR.value
        row.human_review = True
        row.spam_reason = reason
        session.commit()


@celery_app.task(
    name="agent_pochta.continue_after_human",
    bind=True,
    max_retries=2,
    default_retry_delay=120,
)
def continue_after_human_task(self, row_id: str) -> dict:
    """Обзор + 1С после подтверждения отдела оператором (выход из серой зоны)."""
    from agent_pochta.schemas import SpamResult
    from agent_pochta.workers.hitl import continue_after_human_approval
    from agent_pochta.workers.runtime import get_worker_container

    factory = get_session_factory()
    container = get_worker_container()
    row_uuid = uuid.UUID(row_id)

    with factory() as session:
        repo = EmailRepository(session)
        row = repo.get_by_id(row_uuid)
        if row is None:
            return {"ok": False, "reason": "not_found"}
        email = repo.load_email_from_row(row)
        routing = repo.build_routing_from_row(row)
        if email is None:
            return {"ok": False, "reason": "no_payload"}
        if routing is None:
            _fail_post_hitl_processing(
                row_uuid,
                reason="Не удалось продолжить обработку: отсутствует маршрутизация",
            )
            return {"ok": False, "reason": "no_routing"}

        spam: SpamResult | None = None
        if row.is_spam:
            spam = SpamResult(
                is_spam=False,
                confidence=row.spam_confidence or 0.0,
                reason="Подтверждено оператором (не спам)",
            )

        summary_ru = (row.summary_ru or "").strip() or None
        hitl_meta = _hitl_meta_from_row(row)

        row.status = ProcessingStatus.PROCESSING.value
        row.human_review = False
        session.commit()

    try:
        result = continue_after_human_approval(
            email=email,
            routing=routing,
            container=container,
            spam=spam,
            summary_ru=summary_ru,
            meta=hitl_meta,
        )
    except Exception as exc:
        logger.exception("continue_after_human_failed", row_id=row_id)
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc) from exc
        _fail_post_hitl_processing(
            row_uuid,
            reason=f"Сбой после подтверждения оператором: {exc}",
        )
        return {"ok": False, "reason": str(exc), "message_id": email.message_id}

    _sync_post_hitl_result(row_uuid, result)

    erp = result.get("erp")
    meta = result.get("meta") or {}
    if erp and not erp.success and meta.get("erp_retry_scheduled"):
        _schedule_erp_retry(email.message_id)

    return {
        "ok": True,
        "status": str(result.get("status")),
        "message_id": email.message_id,
        "summary_ru": result.get("summary_ru"),
        "trace": result.get("trace", []),
    }


@celery_app.task(name="agent_pochta.fetch_email_body")
def fetch_email_body_task(row_id: str) -> dict:
    """Загружает тело письма из IMAP и кеширует в raw_payload_json для UI."""
    from agent_pochta.imap.body_fetch import fetch_and_cache_email_body

    result = fetch_and_cache_email_body(uuid.UUID(row_id))
    return {
        "ok": result.ok,
        "row_id": result.row_id,
        "body_text": result.body_text,
        "body_html": result.body_html,
        "reason": result.reason,
        "cached": result.cached,
    }


@celery_app.task(name="agent_pochta.export_statistics")
def export_statistics_task() -> dict:
    """Периодически пересобирает статистика.json / статистика.md из change_events."""
    from agent_pochta.stats.export import export_statistics_files

    try:
        return export_statistics_files()
    except Exception as exc:
        logger.exception("export_statistics_failed")
        return {"ok": False, "error": str(exc)}


@celery_app.task(name="agent_pochta.sync_rag_to_qdrant")
def sync_rag_to_qdrant_task() -> dict:
    """Резервная hourly-синхронизация HITL JSON / PostgreSQL → Qdrant."""
    from agent_pochta.config import PROJECT_ROOT, get_settings

    settings = get_settings()
    if settings.rag_backend != "qdrant":
        return {"ok": True, "skipped": True, "reason": "stub_backend"}

    try:
        import importlib.util

        script = PROJECT_ROOT / "scripts" / "sync_rag_to_qdrant.py"
        spec = importlib.util.spec_from_file_location("sync_rag_to_qdrant", script)
        if spec is None or spec.loader is None:
            return {"ok": False, "error": f"cannot load {script}"}
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        result = {
            "spam_learning": mod.sync_spam_learning_from_json(),
            "onec_corrections": mod.sync_onec_corrections_from_json(),
            "contractors": mod.sync_contractors_from_db(),
            "rag_keywords": mod.apply_rag_department_keywords(),
            "routing_keywords": mod.apply_routing_correction_keywords(),
        }
        logger.info("sync_rag_to_qdrant_done", **{k: bool(v) for k, v in result.items()})
        return {"ok": True, **result}
    except Exception as exc:
        logger.exception("sync_rag_to_qdrant_failed")
        return {"ok": False, "error": str(exc)}


@celery_app.task(name="agent_pochta.index_email_vectors", bind=True, max_retries=3, default_retry_delay=120)
def index_email_vectors_task(self, row_id: str, *, force: bool = False) -> dict:
    """Векторизация письма (тело + вложения) в Qdrant через BGE."""
    from agent_pochta.services.email_indexing import index_email_by_id

    try:
        result = index_email_by_id(row_id, force=force)
        if not result.get("ok") and result.get("error") and not result.get("skipped"):
            raise RuntimeError(result["error"])
        return result
    except Exception as exc:
        logger.exception("index_email_vectors_failed", row_id=row_id)
        if self.request.retries >= self.max_retries:
            return {"ok": False, "row_id": row_id, "error": str(exc)}
        raise self.retry(exc=exc) from exc


@celery_app.task(name="agent_pochta.sync_emails_to_qdrant")
def sync_emails_to_qdrant_task(*, limit: int | None = None, force: bool = False) -> dict:
    """Backfill: индексирует необработанные письма в Qdrant (BGE)."""
    from agent_pochta.config import PROJECT_ROOT, get_settings

    settings = get_settings()
    if not settings.email_rag_enabled or settings.rag_backend != "qdrant":
        return {"ok": True, "skipped": True, "reason": "disabled_or_stub"}

    try:
        import importlib.util

        script = PROJECT_ROOT / "scripts" / "sync_emails_to_qdrant.py"
        spec = importlib.util.spec_from_file_location("sync_emails_to_qdrant", script)
        if spec is None or spec.loader is None:
            return {"ok": False, "error": f"cannot load {script}"}
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.sync_pending_emails(limit=limit or settings.email_rag_sync_batch_size, force=force)
    except Exception as exc:
        logger.exception("sync_emails_to_qdrant_failed")
        return {"ok": False, "error": str(exc)}


@celery_app.task(name="agent_pochta.index_department_correction", bind=True, max_retries=3, default_retry_delay=120)
def index_department_correction_task(
    self,
    row_id: str,
    *,
    wrong_dept_id: str | None = None,
    wrong_dept_name: str | None = None,
    correct_dept_id: str,
    correct_dept_name: str,
    reextract: bool = True,
) -> dict:
    """Realtime upsert operator correction into department_corrections_bge."""
    from agent_pochta.services.bge_correction_learning import upsert_correction_by_email_id

    try:
        result = upsert_correction_by_email_id(
            row_id,
            wrong_dept_id=wrong_dept_id,
            wrong_dept_name=wrong_dept_name,
            correct_dept_id=correct_dept_id,
            correct_dept_name=correct_dept_name,
            reextract=reextract,
        )
        if not result.get("ok") and not result.get("skipped"):
            raise RuntimeError(result.get("reason") or result.get("error") or "upsert_failed")
        return result
    except Exception as exc:
        logger.exception("index_department_correction_failed row_id=%s", row_id)
        if self.request.retries >= self.max_retries:
            return {"ok": False, "row_id": row_id, "error": str(exc)}
        raise self.retry(exc=exc) from exc


@celery_app.task(name="agent_pochta.index_operator_verified", bind=True, max_retries=3, default_retry_delay=120)
def index_operator_verified_task(
    self,
    row_id: str,
    *,
    reextract: bool = True,
) -> dict:
    """Realtime upsert operator-verified email into department_corrections_bge."""
    from agent_pochta.services.bge_correction_learning import upsert_verified_by_email_id

    try:
        result = upsert_verified_by_email_id(row_id, reextract=reextract)
        if not result.get("ok") and not result.get("skipped"):
            raise RuntimeError(result.get("reason") or result.get("error") or "upsert_failed")
        return result
    except Exception as exc:
        logger.exception("index_operator_verified_failed row_id=%s", row_id)
        if self.request.retries >= self.max_retries:
            return {"ok": False, "row_id": row_id, "error": str(exc)}
        raise self.retry(exc=exc) from exc


@celery_app.task(name="agent_pochta.sync_department_corrections_to_qdrant")
def sync_department_corrections_to_qdrant_task(*, limit: int | None = None) -> dict:
    """Backfill department corrections into Qdrant via BGE."""
    from agent_pochta.config import PROJECT_ROOT, get_settings

    settings = get_settings()
    if not settings.email_rag_enabled or settings.rag_backend != "qdrant":
        return {"ok": True, "skipped": True, "reason": "disabled_or_stub"}

    try:
        import importlib.util

        script = PROJECT_ROOT / "scripts" / "sync_department_corrections_to_qdrant.py"
        spec = importlib.util.spec_from_file_location("sync_department_corrections_to_qdrant", script)
        if spec is None or spec.loader is None:
            return {"ok": False, "error": f"cannot load {script}"}
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.sync_all(limit=limit or settings.dept_corrections_sync_batch_size)
    except Exception as exc:
        logger.exception("sync_department_corrections_to_qdrant_failed")
        return {"ok": False, "error": str(exc)}


@celery_app.task(name="agent_pochta.eval_bge_routing_holdout")
def eval_bge_routing_holdout_task(*, limit: int = 100) -> dict:
    """Nightly holdout eval → data/stats/bge_holdout_eval.json + Prometheus gauge."""
    from agent_pochta.config import PROJECT_ROOT, get_settings
    from agent_pochta.metrics.prometheus_exporter import refresh_prometheus_metrics

    settings = get_settings()
    if not settings.embedding_base_url or settings.rag_backend != "qdrant":
        return {"ok": True, "skipped": True, "reason": "disabled_or_stub"}

    try:
        import importlib.util

        script = PROJECT_ROOT / "scripts" / "eval_bge_routing_holdout.py"
        spec = importlib.util.spec_from_file_location("eval_bge_routing_holdout", script)
        if spec is None or spec.loader is None:
            return {"ok": False, "error": f"cannot load {script}"}
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        result = mod.evaluate(limit=limit)
        refresh_prometheus_metrics()
        return {"ok": True, **result}
    except SystemExit as exc:
        return {"ok": False, "exit_code": int(exc.code or 1)}
    except Exception as exc:
        logger.exception("eval_bge_routing_holdout_failed")
        return {"ok": False, "error": str(exc)}


@celery_app.task(
    name="agent_pochta.reprocess_message",
    bind=True,
    max_retries=2,
    default_retry_delay=120,
)
def reprocess_message_task(
    self,
    row_id: str,
    *,
    restored_from_spam: bool = False,
    reanalyze: bool = False,
) -> dict:
    """Повторный прогон графа (восстановление из спама / повторный LLM-анализ / ручной перезапуск)."""
    factory = get_session_factory()
    row_uuid = uuid.UUID(row_id)
    with factory() as session:
        repo = EmailRepository(session)
        row = repo.get_by_id(row_uuid)
        if row is None:
            return {"ok": False, "reason": "not_found"}
        email = repo.load_email_from_row(row)
        if email is None:
            return {"ok": False, "reason": "no_payload"}
        if restored_from_spam:
            learn_from_not_spam(
                message_id=row.message_id,
                sender_email=email.sender_email,
                subject=email.subject or "",
                body=repo.learning_text_from_row(row, email),
                reason="Восстановлено из спама оператором",
            )
            repo.mark_restored_from_spam(row.id)
        session.commit()

    meta: dict = {}
    if restored_from_spam:
        meta["restored_from_spam"] = True
    if reanalyze:
        meta["reanalyze"] = True
    persist_processing_start(email)
    graph = get_worker_graph()
    try:
        result = graph.invoke({"email": email, "meta": meta})
    except Exception as exc:
        logger.exception("reprocess_message_failed", row_id=row_id)
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc) from exc
        _fail_reprocessing(
            row_uuid,
            reason=f"Сбой повторной обработки: {exc}",
        )
        return {"ok": False, "reason": str(exc), "message_id": email.message_id}
    return {"ok": True, "status": str(result.get("status")), "message_id": email.message_id}
