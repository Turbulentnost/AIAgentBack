"""IMAP-мониторинг входящей почты (ТЗ §4.1 узел 1, §5.1)."""

from agent_pochta.imap.client import ImapMailboxClient, fetch_unseen_messages
from agent_pochta.imap.poller import poll_mailboxes

__all__ = ["ImapMailboxClient", "fetch_unseen_messages", "poll_mailboxes"]
