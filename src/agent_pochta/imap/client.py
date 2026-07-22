"""IMAP-клиент: polling непрочитанных писем (порт 993, SSL/TLS)."""

from __future__ import annotations

import ssl
from dataclasses import dataclass
from datetime import date

from imapclient import IMAPClient

try:
    from billiard.exceptions import SoftTimeLimitExceeded
except ImportError:  # pragma: no cover — outside Celery worker
    SoftTimeLimitExceeded = None  # type: ignore[misc, assignment]

from agent_pochta.config import Settings, get_settings
from agent_pochta.imap.parser import parse_raw_email
from agent_pochta.imap.attachment_parts import ImapAttachmentPart, list_attachment_parts
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
            # Newest-first: SINCE on busy mailboxes (e.g. info@) can match thousands of UIDs.
            scan_uids = sorted(uids, reverse=True)
            max_scan = max(0, int(getattr(self.settings, "imap_catchup_max_uids", 50) or 0))
            if max_scan:
                if exclude:
                    # Scan a wider header window, but stop after max_scan unknown targets.
                    header_cap = max(max_scan * 10, 200)
                    scan_uids = scan_uids[:header_cap]
                else:
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
                        if max_scan and len(target_uids) >= max_scan:
                            break
                    if max_scan and len(target_uids) >= max_scan:
                        break

            emails: list[EmailMessage] = []
            try:
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
            except Exception as exc:
                if SoftTimeLimitExceeded is None or not isinstance(exc, SoftTimeLimitExceeded):
                    raise
            return emails
        finally:
            try:
                client.logout()
            except Exception:
                pass
    def _find_uid_by_message_id(self, client: IMAPClient, message_id: str, *, folder: str) -> int | None:
        header_id = imap_header_message_id(message_id)
        bare_id = header_id.strip("<>")
        client.select_folder(folder, readonly=True)
        for candidate in (header_id, bare_id):
            uids = client.search(["HEADER", "Message-ID", candidate])
            if uids:
                return max(uids)
        return None

    @staticmethod
    def _extract_fetch_bytes(fetch_item: dict, needle: str) -> bytes | None:
        needle_upper = needle.upper()
        for key, value in fetch_item.items():
            key_text = key.decode("utf-8", errors="replace") if isinstance(key, bytes) else str(key)
            if needle_upper not in key_text.upper():
                continue
            if isinstance(value, (bytes, bytearray)):
                return bytes(value)
            if isinstance(value, tuple) and value:
                first = value[0]
                if isinstance(first, (bytes, bytearray)):
                    return bytes(first)
        return None

    def _fetch_bodystructure(self, client: IMAPClient, uid: int) -> object | None:
        fetch_data = client.fetch([uid], ["BODYSTRUCTURE"])
        item = fetch_data.get(uid) or {}
        for key, value in item.items():
            key_text = key.decode("utf-8", errors="replace") if isinstance(key, bytes) else str(key)
            if "BODYSTRUCTURE" in key_text.upper():
                return value
        return item.get(b"BODYSTRUCTURE") or item.get("BODYSTRUCTURE")

    def _fetch_body_part(self, client: IMAPClient, uid: int, part_id: str) -> bytes | None:
        fetch_data = client.fetch([uid], [f"BODY.PEEK[{part_id}]"])
        item = fetch_data.get(uid) or {}
        return self._extract_fetch_bytes(item, f"BODY[{part_id}]") or self._extract_fetch_bytes(item, "BODY")

    @staticmethod
    def _pick_attachment_part(
        parts: list[ImapAttachmentPart],
        *,
        filename: str,
        index: int,
    ) -> ImapAttachmentPart | None:
        if not parts:
            return None
        normalized = filename.strip().lower()
        for part in parts:
            if part.filename and part.filename.strip().lower() == normalized:
                return part
        if 0 <= index < len(parts):
            return parts[index]
        return parts[0]

    def fetch_attachment_bytes(
        self,
        message_id: str,
        *,
        filename: str,
        attachment_index: int = 0,
        folder: str = "INBOX",
        timeout_sec: int | None = None,
    ) -> tuple[bytes, str, str] | None:
        """Загружает одно вложение: сначала BODY.PEEK[part], иначе полный RFC822."""
        client = self._connect(timeout_sec=timeout_sec)
        try:
            uid = self._find_uid_by_message_id(client, message_id, folder=folder)
            if uid is None:
                return None

            settings = self.settings
            if settings.attachment_imap_partial_fetch:
                structure = self._fetch_bodystructure(client, uid)
                if structure is not None:
                    parts = list_attachment_parts(structure)
                    chosen = self._pick_attachment_part(
                        parts,
                        filename=filename,
                        index=attachment_index,
                    )
                    if chosen is not None:
                        content = self._fetch_body_part(client, uid, chosen.part_id)
                        if content:
                            mime_type = chosen.mime_type or "application/octet-stream"
                            resolved_name = chosen.filename or filename
                            return content, mime_type, resolved_name

            fetch_data = client.fetch([uid], ["RFC822"])
            raw = fetch_data[uid][b"RFC822"]
            email = parse_raw_email(raw, self.mailbox, load_oversized_attachments=True)
            by_name = {a.filename: a for a in email.attachments if a.filename}
            matched = by_name.get(filename)
            if matched is None and 0 <= attachment_index < len(email.attachments):
                matched = email.attachments[attachment_index]
            if matched is None or not matched.content:
                return None
            return (
                matched.content,
                matched.mime_type or "application/octet-stream",
                matched.filename or filename,
            )
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