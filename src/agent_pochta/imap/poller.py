"""Опрос почтовых ящиков и постановка писем в очередь Celery."""

from __future__ import annotations

import structlog

from agent_pochta.config import get_settings
from agent_pochta.demo_filter import is_demo_email
from agent_pochta.email_payload import email_to_task_payload
from agent_pochta.imap.client import fetch_unseen_messages
from agent_pochta.services import ServiceContainer, build_container

logger = structlog.get_logger(__name__)


def poll_mailboxes(container: ServiceContainer | None = None) -> dict:
    """Опрашивает все ящики из MAILBOXES и ставит новые письма в Celery."""
    from agent_pochta.workers.runtime import get_worker_container
    from agent_pochta.workers.tasks import process_email_task, should_enqueue_email

    settings = get_settings()
    container = container or get_worker_container()
    enqueued = 0
    skipped_demo = 0
    skipped_processed = 0
    errors: list[str] = []

    for mailbox in settings.mailbox_list:
        mailbox_enqueued = 0
        mailbox_skipped_processed = 0
        try:
            emails = fetch_unseen_messages(mailbox, container.vault, settings=settings)
            for email in emails:
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
                fetched=len(emails),
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
        "errors": errors,
    }
