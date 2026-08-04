"""Опрос почтовых ящиков и постановка писем в очередь Celery."""

from __future__ import annotations

from datetime import date, timedelta

import structlog
from sqlalchemy import func

from agent_pochta.config import Settings, get_settings
from agent_pochta.db.models import EmailMessageRow
from agent_pochta.db.session import get_session_factory
from agent_pochta.demo_filter import is_demo_email
from agent_pochta.email_payload import email_to_task_payload
from agent_pochta.imap.client import fetch_since_messages, fetch_unseen_messages
from agent_pochta.schemas import EmailMessage, ProcessingStatus
from agent_pochta.services import ServiceContainer, build_container

logger = structlog.get_logger(__name__)


def merge_emails_by_message_id(*email_lists: list[EmailMessage]) -> list[EmailMessage]:
    """Объединяет списки писем без дубликатов по message_id."""
    seen: set[str] = set()
    merged: list[EmailMessage] = []
    for emails in email_lists:
        for email in emails:
            if email.message_id in seen:
                continue
            seen.add(email.message_id)
            merged.append(email)
    return merged


def catchup_since_date(
    last_received_at,
    *,
    settings: Settings | None = None,
) -> date:
    """Дата начала догоняющего IMAP-опроса (SINCE) при простое сервиса."""
    settings = settings or get_settings()
    if last_received_at is None:
        return date.today() - timedelta(days=settings.imap_catchup_days)
    return last_received_at.date() - timedelta(days=1)


def poll_mailboxes(container: ServiceContainer | None = None) -> dict:
    """Опрашивает все ящики из MAILBOXES и ставит новые письма в Celery."""
    from agent_pochta.workers.runtime import get_worker_container
    from agent_pochta.workers.tasks import process_email_task, should_enqueue_email

    settings = get_settings()
    container = container or get_worker_container()
    enqueued = 0
    skipped_demo = 0
    skipped_processed = 0
    skipped_backlog = 0
    errors: list[str] = []

    factory = get_session_factory()
    backlog_paused = False
    processing_count = 0
    with factory() as session:
        processing_count = (
            session.query(func.count(EmailMessageRow.id))
            .filter(EmailMessageRow.status == ProcessingStatus.PROCESSING.value)
            .scalar()
            or 0
        )
        backlog_paused = processing_count >= settings.processing_backlog_pause_threshold
        max_enqueue = settings.imap_poll_max_enqueue if not backlog_paused else 0

        if backlog_paused:
            logger.info(
                "imap_poll_backlog_paused",
                processing_count=processing_count,
                threshold=settings.processing_backlog_pause_threshold,
            )

        for mailbox in settings.mailbox_list:
            mailbox_enqueued = 0
            mailbox_skipped_processed = 0
            mailbox_fetched = 0
            try:
                last_received_at = (
                    session.query(func.max(EmailMessageRow.received_at))
                    .filter(EmailMessageRow.mailbox == mailbox)
                    .scalar()
                )
                since = catchup_since_date(last_received_at, settings=settings)
                unseen_emails = fetch_unseen_messages(mailbox, container.vault, settings=settings)
                known_bases = {
                    (mid or "").split("#", 1)[0]
                    for (mid,) in (
                        session.query(EmailMessageRow.message_id)
                        .filter(EmailMessageRow.mailbox == mailbox)
                        .all()
                    )
                    if mid
                }
                catchup_emails: list[EmailMessage] = []
                if not backlog_paused:
                    try:
                        catchup_emails = fetch_since_messages(
                            mailbox,
                            container.vault,
                            since,
                            settings=settings,
                            mark_seen=False,
                            exclude_message_id_bases=known_bases,
                        )
                    except Exception as catchup_exc:
                        # Do not block new UNSEEN mail if catch-up FETCH fails server-side.
                        errors.append(f"{mailbox}: catchup {catchup_exc}")
                        logger.exception(
                            "imap_catchup_failed",
                            mailbox=mailbox,
                            catchup_since=since.isoformat(),
                        )
                emails = merge_emails_by_message_id(unseen_emails, catchup_emails)
                mailbox_fetched = len(emails)
                for email in emails:
                    if enqueued >= max_enqueue:
                        skipped_backlog += 1
                        continue
                    if is_demo_email(email):
                        skipped_demo += 1
                        logger.info(
                            "imap_skip_demo_email",
                            mailbox=mailbox,
                            message_id=email.message_id,
                            sender=email.sender_email,
                        )
                        continue
                    if not should_enqueue_email(email):
                        skipped_processed += 1
                        mailbox_skipped_processed += 1
                        continue
                    process_email_task.delay(email_to_task_payload(email))
                    enqueued += 1
                    mailbox_enqueued += 1
                logger.info(
                    "imap_poll_mailbox",
                    mailbox=mailbox,
                    fetched=mailbox_fetched,
                    unseen=len(unseen_emails),
                    catchup_since=since.isoformat(),
                    catchup=len(catchup_emails),
                    enqueued=mailbox_enqueued,
                    skipped_processed=mailbox_skipped_processed,
                )
            except Exception as exc:
                errors.append(f"{mailbox}: {exc}")
                logger.exception("imap_poll_failed", mailbox=mailbox)

    return {
        "enqueued": enqueued,
        "skipped_demo": skipped_demo,
        "skipped_processed": skipped_processed,
        "skipped_backlog": skipped_backlog,
        "backlog_paused": backlog_paused,
        "processing_count": processing_count,
        "errors": errors,
    }
