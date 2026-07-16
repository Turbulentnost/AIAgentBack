"""IMAP-клиент: polling непрочитанных писем (порт 993, SSL/TLS)."""

from __future__ import annotations

import ssl
from dataclasses import dataclass
from datetime import date

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

    def _connect(self, *, timeout_sec: int | None = None) -> IMAPClient:
        context = ssl.create_default_context()
        client = IMAPClient(
            self.settings.imap_host,
            port=self.settings.imap_port,
            ssl=True,
            ssl_context=context,
            timeout=timeout_sec if timeout_sec is not None else self.settings.imap_connect_timeout_sec,
        )
        client.login(self.credentials.username, self.credentials.password)
        return client

    def fetch_unseen(self, mark_seen: bool = True) -> list[EmailMessage]:
        """Возвращает непрочитанные письма INBOX."""
        return self._fetch_by_search(["UNSEEN"], mark_seen=mark_seen)

    def fetch_since(
        self,
        since: date,
        *,
        mark_seen: bool = False,
        exclude_message_id_bases: set[str] | None = None,
    ) -> list[EmailMessage]:
        """Fetch INBOX messages since date; skip Message-IDs already known locally."""
        return self._fetch_by_search(
            ["SINCE", since.strftime("%d-%b-%Y")],
            mark_seen=mark_seen,
            exclude_message_id_bases=exclude_message_id_bases,
        )

    def _header_message_id(self, header_bytes: bytes | None) -> str:
        if not header_bytes:
            return ""
        for line in header_bytes.decode("utf-8", errors="replace").splitlines():
            if line.lower().startswith("message-id:"):
                return line.split(":", 1)[1].strip()
        return ""

    def _fetch_by_search(
        self,
        criteria: list,
        *,
        mark_seen: bool,
        exclude_message_id_bases: set[str] | None = None,
    ) -> list[EmailMessage]:
        client = self._connect()
        try:
            client.select_folder("INBOX", readonly=not mark_seen)
            uids = client.search(criteria)
            if not uids:
                return []

            batch_size = max(1, int(getattr(self.settings, "imap_fetch_batch_size", 20) or 20))
            exclude = exclude_message_id_bases or set()
            # Newest-first: mailbox SINCE windows are huge; full header scans hit SoftTimeLimit.
            scan_uids = sorted(uids, reverse=True)
            max_scan = max(0, int(getattr(self.settings, "imap_catchup_max_uids", 400) or 0))
            if max_scan and exclude:
                scan_uids = scan_uids[:max_scan]
            target_uids = list(scan_uids)

            if exclude:
                target_uids = []
                header_batch = max(batch_size, 100)
                for offset in range(0, len(scan_uids), header_batch):
                    batch = scan_uids[offset : offset + header_batch]
                    headers = client.fetch(batch, ["BODY.PEEK[HEADER.FIELDS (MESSAGE-ID)]"])
                    for uid in batch:
                        item = headers.get(uid) or {}
                        header_raw = item.get(b"BODY[HEADER.FIELDS (MESSAGE-ID)]")
                        if header_raw is None:
                            for key, value in item.items():
                                if b"HEADER.FIELDS" in key and isinstance(value, (bytes, bytearray)):
                                    header_raw = value
                                    break
                        mid = self._header_message_id(header_raw)
                        if mid and mid in exclude:
                            continue
                        target_uids.append(uid)

            emails: list[EmailMessage] = []
            for offset in range(0, len(target_uids), batch_size):
                batch = target_uids[offset : offset + batch_size]
                fetch_data = client.fetch(batch, ["RFC822"])
                for uid in batch:
                    item = fetch_data.get(uid)
                    if not item or b"RFC822" not in item:
                        continue
                    emails.append(parse_raw_email(item[b"RFC822"], self.mailbox))
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
        load_oversized_attachments: bool = False,
        timeout_sec: int | None = None,
    ) -> EmailMessage | None:
        """Ищет письмо по заголовку Message-ID и возвращает распарсенное содержимое."""
        header_id = imap_header_message_id(message_id)
        bare_id = header_id.strip("<>")

        client = self._connect(timeout_sec=timeout_sec)
        try:
            client.select_folder(folder, readonly=not mark_seen)
            for candidate in (header_id, bare_id):
                uids = client.search(["HEADER", "Message-ID", candidate])
                if uids:
                    uid = max(uids)
                    fetch_data = client.fetch([uid], ["RFC822"])
                    raw = fetch_data[uid][b"RFC822"]
                    return parse_raw_email(
                        raw,
                        self.mailbox,
                        load_oversized_attachments=load_oversized_attachments,
                    )
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


def fetch_since_messages(
    mailbox: str,
    vault: VaultClient,
    since: date,
    settings: Settings | None = None,
    *,
    mark_seen: bool = False,
    exclude_message_id_bases: set[str] | None = None,
) -> list[EmailMessage]:
    credentials = resolve_imap_credentials(mailbox, vault)
    client = ImapMailboxClient(mailbox, credentials, settings=settings)
    return client.fetch_since(
        since,
        mark_seen=mark_seen,
        exclude_message_id_bases=exclude_message_id_bases,
    )