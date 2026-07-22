"""Lazy-загрузка бинарного содержимого вложений из IMAP (reprocess / UI fetch body)."""

from __future__ import annotations

import structlog

from agent_pochta.imap.client import ImapMailboxClient, resolve_imap_credentials
from agent_pochta.schemas import EmailMessage
from agent_pochta.services.vault import VaultClient

logger = structlog.get_logger(__name__)


def ensure_attachments_from_imap(
    email: EmailMessage,
    vault: VaultClient,
    *,
    load_oversized: bool = False,
) -> int:
    """Подгружает bytes вложений из IMAP, если они отсутствуют в памяти/БД."""
    missing = [a for a in email.attachments if a.content is None and a.size_bytes > 0]
    if not missing or not email.mailbox:
        return 0

    imap_id = email.message_id.split("#", 1)[0]
    try:
        credentials = resolve_imap_credentials(email.mailbox, vault)
        client = ImapMailboxClient(email.mailbox, credentials)
        fresh = client.fetch_by_message_id(
            imap_id,
            load_oversized_attachments=load_oversized,
        )
    except Exception as exc:
        logger.warning(
            "attachment_imap_fetch_failed",
            message_id=email.message_id,
            mailbox=email.mailbox,
            error=str(exc),
        )
        return 0

    if fresh is None:
        logger.info(
            "attachment_imap_fetch_not_found",
            message_id=email.message_id,
            mailbox=email.mailbox,
        )
        return 0

    by_name = {a.filename: a for a in fresh.attachments if a.filename}
    restored = 0
    for att in missing:
        fresh_att = by_name.get(att.filename)
        if fresh_att and fresh_att.content:
            att.content = fresh_att.content
            restored += 1
            logger.info(
                "attachment_imap_fetch_restored",
                filename=att.filename,
                size_bytes=len(fresh_att.content),
            )

    if missing and restored == 0:
        logger.warning(
            "attachment_imap_fetch_no_match",
            message_id=email.message_id,
            missing=[a.filename for a in missing],
            imap_files=list(by_name),
        )
    return restored
