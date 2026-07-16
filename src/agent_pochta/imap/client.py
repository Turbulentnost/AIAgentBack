"""IMAP-клиент: polling непрочитанных писем (порт 993, SSL/TLS)."""

from __future__ import annotations

import ssl
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from imapclient import IMAPClient

from agent_pochta.config import Settings, get_settings
from agent_pochta.imap.parser import parse_raw_email
from agent_pochta.schemas import EmailMessage
from agent_pochta.services.vault import VaultClient


def imap_header_message_id(message_id: str) -> str:
    """Нормализует Message-ID для IMAP SEARCH HEADER."""
    base = message_id.split("#", 1)[0].strip()
    if base.startswith("<") and base.endswith(">"):
        return base
    if base.startswith("<"):
        return f"{base}>"
    if base.endswith(">"):
        return f"<{base}"
    return f"<{base}>"


@dataclass(frozen=True)
class ImapCredentials:
    username: str
    password: str


def _mailbox_env_key(mailbox: str) -> str:
    return mailbox.replace("@", "_").replace(".", "_").replace("-", "_").upper()


def resolve_imap_credentials(mailbox: str, vault: VaultClient) -> ImapCredentials:
    """Логин/пароль из Settings (.env), Vault или переменных окружения."""
    settings = get_settings()
    mailbox_key = _mailbox_env_key(mailbox)

    username = (
        vault.get_secret(f"IMAP_USER_{mailbox_key}")
        or settings.imap_username
        or vault.get_secret("IMAP_USERNAME")
        or mailbox
    )
    password = (
        vault.get_secret(f"IMAP_PASSWORD_{mailbox_key}")
        or settings.imap_password
        or vault.get_secret("IMAP_PASSWORD")
    )
    if not password:
        raise ValueError(
            f"IMAP password not configured for {mailbox}. "
            f"Add IMAP_PASSWORD to .env (shared) or IMAP_PASSWORD_{mailbox_key} per mailbox."
        )
    return ImapCredentials(username=username, password=password)


class ImapMailboxClient:
    """Подключение к одному почтовому ящику."""

    def __init__(
        self,
        mailbox: str,
        credentials: ImapCredentials,
        settings: Settings | None = None,
    ) -> None:
        self.mailbox = mailbox
        self.credentials = credentials
        self.settings = settings or get_settings()

    def _connect(self) -> "IMAPClient":
        try:
            from imapclient import IMAPClient
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "IMAP support requires the optional dependency 'imapclient'. "
                "Install it before polling mailboxes or fetching message bodies."
            ) from exc

        context = ssl.create_default_context()
        client = IMAPClient(
            self.settings.imap_host,
            port=self.settings.imap_port,
            ssl=True,
            ssl_context=context,
            timeout=self.settings.imap_connect_timeout_sec,
        )
        client.login(self.credentials.username, self.credentials.password)
        return client

    def fetch_unseen(self, mark_seen: bool = True) -> list[EmailMessage]:
        """Возвращает непрочитанные письма INBOX."""
        client = self._connect()
        try:
            client.select_folder("INBOX", readonly=not mark_seen)
            uids = client.search(["UNSEEN"])
            if not uids:
                return []

            fetch_data = client.fetch(uids, ["RFC822"])
            emails: list[EmailMessage] = []
            for uid in uids:
                raw = fetch_data[uid][b"RFC822"]
                emails.append(parse_raw_email(raw, self.mailbox))
                if mark_seen:
                    client.add_flags([uid], [b"\\Seen"])
            return emails
        finally:
            try:
                client.logout()
            except Exception:
                pass

    def fetch_by_message_id(
        self,
        message_id: str,
        *,
        folder: str = "INBOX",
        mark_seen: bool = False,
    ) -> EmailMessage | None:
        """Ищет письмо по заголовку Message-ID и возвращает распарсенное содержимое."""
        header_id = imap_header_message_id(message_id)
        bare_id = header_id.strip("<>")

        client = self._connect()
        try:
            client.select_folder(folder, readonly=not mark_seen)
            for candidate in (header_id, bare_id):
                uids = client.search(["HEADER", "Message-ID", candidate])
                if uids:
                    uid = max(uids)
                    fetch_data = client.fetch([uid], ["RFC822"])
                    raw = fetch_data[uid][b"RFC822"]
                    return parse_raw_email(raw, self.mailbox)
            return None
        finally:
            try:
                client.logout()
            except Exception:
                pass


def fetch_unseen_messages(
    mailbox: str,
    vault: VaultClient,
    settings: Settings | None = None,
) -> list[EmailMessage]:
    credentials = resolve_imap_credentials(mailbox, vault)
    client = ImapMailboxClient(mailbox, credentials, settings=settings)
    return client.fetch_unseen()
