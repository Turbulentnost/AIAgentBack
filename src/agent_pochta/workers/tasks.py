"""Celery-задачи: обработка письма, IMAP poll, retry 1С."""

from __future__ import annotations

import json
import uuid

import structlog

from agent_pochta.config import get_settings
from agent_pochta.db.models import EmailMessageRow
from agent_pochta.db.repository import EmailRepository
from agent_pochta.db.session import get_session_factory
from agent_pochta.demo_filter import is_demo_email
from agent_pochta.email_payload import email_from_task_payload
from agent_pochta.routing.recipients import routing_message_id, split_routing_recipients
from agent_pochta.schemas import EmailMessage, ProcessingStatus
from agent_pochta.routing.learning import learn_from_not_spam
from agent_pochta.workers.celery_app import celery_app
from agent_pochta.workers.runtime import get_worker_graph

logger = structlog.get_logger(__name__)

_TERMINAL_STATUSES = {
    ProcessingStatus.DONE.value,
    ProcessingStatus.SPAM.value,
    ProcessingStatus.AWAITING_HUMAN.value,
}


def _is_already_processed(message_id: str) -> bool:
    factory = get_session_factory()
    try:
        with factory() as session:
            row = session.query(EmailMessageRow).filter_by(message_id=message_id).one_or_none()
    except Exception:
        return False
    if row is None:
        return False
    return row.status in _TERMINAL_STATUSES


def should_enqueue_email(email: EmailMessage) -> bool:
    """False, если все получатели письма уже в терминальном статусе в БД."""
    recipients = split_routing_recipients(email)
    for recipient in recipients:
        attempt_id = routing_message_id(email.message_id, recipient)
        if not _is_already_processed(attempt_id):
            return True
    return False


def _schedule_erp_retry(message_id: str) -> None:
    settings = get_settings()
    retry_erp_task.apply_async(args=[message_id], countdown=settings.erp_retry_delay_sec)


@celery_app.task(name="agent_pochta.process_email", bind=True, max_retries=3, default_retry_delay=60)
def process_email_task(self, email_payload: dict) -> dict:
    email = email_from_task_payload(email_payload)
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

        if _is_already_processed(attempt_id):
            results.append(
                {"skipped": True, "message_id": attempt_id, "recipient": recipient, "reason": "already_processed"}
            )
            continue

        graph = get_worker_graph()
        result = graph.invoke({"email": attempt_email})

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


@celery_app.task(name="agent_pochta.poll_imap", queue="imap")
def poll_imap_task() -> dict:
    from agent_pochta.imap.poller import poll_mailboxes

    return poll_mailboxes()


@celery_app.task(name="agent_pochta.retry_erp", bind=True)
def retry_erp_task(self, message_id: str) -> dict:
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

        attempt = repo.increment_erp_retry(row.id)
        xml_document = None
        if row.raw_payload_json:
            try:
                loaded = json.loads(row.raw_payload_json)
                if isinstance(loaded, dict):
                    xml_document = loaded.get("xml_document")
            except json.JSONDecodeError:
                xml_document = None
        session.commit()

    try:
        res = container.integration.create_incoming_correspondence(
            email, routing, summary_ru, xml_document=xml_document
        )
        with factory() as session:
            row = EmailRepository(session).get_by_message_id(message_id)
            if row:
                row.erp_document_number = res["erp_document_number"]
                row.erp_task_id = res["erp_task_id"]
                row.status = ProcessingStatus.DONE.value
                row.human_review = False
                row.erp_retry_count = 0
                session.commit()
        return {"ok": True, "attempt": attempt, "erp_document_number": res["erp_document_number"]}
    except Exception as exc:
        logger.exception("erp_retry_failed", message_id=message_id, attempt=attempt)
        with factory() as session:
            row = EmailRepository(session).get_by_message_id(message_id)
            if row and row.erp_retry_count < settings.erp_retry_max:
                session.commit()
                retry_erp_task.apply_async(args=[message_id], countdown=settings.erp_retry_delay_sec)
            elif row:
                row.status = ProcessingStatus.ERROR.value
                row.human_review = True
                session.commit()
        return {"ok": False, "attempt": attempt, "error": str(exc)}


@celery_app.task(name="agent_pochta.continue_after_human")
def continue_after_human_task(row_id: str) -> dict:
    """Обзор + 1С после подтверждения отдела оператором (выход из серой зоны)."""
    from agent_pochta.schemas import SpamResult
    from agent_pochta.workers.hitl import continue_after_human_approval
    from agent_pochta.workers.runtime import get_worker_container

    factory = get_session_factory()
    container = get_worker_container()

    with factory() as session:
        repo = EmailRepository(session)
        row = repo.get_by_id(uuid.UUID(row_id))
        if row is None:
            return {"ok": False, "reason": "not_found"}
        email = repo.load_email_from_row(row)
        routing = repo.build_routing_from_row(row)
        if email is None:
            return {"ok": False, "reason": "no_payload"}
        if routing is None:
            return {"ok": False, "reason": "no_routing"}

        spam: SpamResult | None = None
        if row.is_spam:
            spam = SpamResult(
                is_spam=False,
                confidence=row.spam_confidence or 0.0,
                reason="Подтверждено оператором (не спам)",
            )

        row.status = ProcessingStatus.PROCESSING.value
        row.human_review = False
        session.commit()

    result = continue_after_human_approval(
        email=email,
        routing=routing,
        container=container,
        spam=spam,
    )
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


@celery_app.task(name="agent_pochta.reprocess_message")
def reprocess_message_task(row_id: str, *, restored_from_spam: bool = False) -> dict:
    """Повторный прогон графа (восстановление из спама / ручной перезапуск)."""
    factory = get_session_factory()
    with factory() as session:
        repo = EmailRepository(session)
        row = repo.get_by_id(uuid.UUID(row_id))
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

    meta = {"restored_from_spam": restored_from_spam} if restored_from_spam else {}
    graph = get_worker_graph()
    result = graph.invoke({"email": email, "meta": meta})
    return {"ok": True, "status": str(result.get("status")), "message_id": email.message_id}
