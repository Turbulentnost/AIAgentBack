"""Загрузка тела письма из IMAP по Message-ID (on-demand для UI)."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass

from agent_pochta.config import get_settings
from agent_pochta.db.repository import EmailRepository
from agent_pochta.db.session import get_session_factory
from agent_pochta.email_payload import BODY_NOT_STORED_PLACEHOLDER
from agent_pochta.imap.client import ImapMailboxClient, resolve_imap_credentials
from agent_pochta.services.vault import VaultClient


class EmailBodyFetchError(Exception):
    """Базовая ошибка загрузки тела письма."""


class EmailBodyNotFoundError(EmailBodyFetchError):
    """Письмо не найдено в почтовом ящике."""


class EmailBodyRecordError(EmailBodyFetchError):
    """Запись письма в БД недоступна или неполная."""


@dataclass(frozen=True)
class FetchEmailBodyResult:
    ok: bool
    row_id: str
    body_text: str = ""
    body_html: str | None = None
    reason: str | None = None
    cached: bool = False


def payload_body_text(payload: dict) -> str:
    body = str(payload.get("body_text") or "").strip()
    if body:
        return body
    body_html = payload.get("body_html")
    if body_html:
        from agent_pochta.imap.parser import _html_to_text

        return _html_to_text(str(body_html))
    return ""


def row_has_cached_body(row) -> bool:
    if not row.raw_payload_json:
        return False
    try:
        payload = json.loads(row.raw_payload_json)
    except json.JSONDecodeError:
        return False
    if not isinstance(payload, dict):
        return False
    text = payload_body_text(payload)
    return bool(text) and text != BODY_NOT_STORED_PLACEHOLDER


def resolve_imap_message_id(row) -> str:
    if row.raw_payload_json:
        try:
            payload = json.loads(row.raw_payload_json)
            if isinstance(payload, dict):
                stored = str(payload.get("message_id") or "").strip()
                if stored:
                    return stored.split("#", 1)[0]
        except json.JSONDecodeError:
            pass
    return row.message_id.split("#", 1)[0]


def fetch_message_from_imap(
    *,
    mailbox: str,
    message_id: str,
    vault: VaultClient,
) -> tuple[str, str | None]:
    settings = get_settings()
    credentials = resolve_imap_credentials(mailbox, vault)
    client = ImapMailboxClient(mailbox, credentials, settings=settings)
    email = client.fetch_by_message_id(message_id)
    if email is None:
        raise EmailBodyNotFoundError(
            f"Message {message_id!r} not found in mailbox {mailbox!r}"
        )
    return email.body_text, email.body_html


def fetch_and_cache_email_body(
    row_id: uuid.UUID,
    *,
    vault: VaultClient | None = None,
) -> FetchEmailBodyResult:
    """Загружает тело из IMAP и кеширует в raw_payload_json (без повторного sanitize)."""
    from agent_pochta.workers.runtime import get_worker_container

    factory = get_session_factory()
    container = get_worker_container() if vault is None else None
    vault = vault or (container.vault if container else VaultClient())

    with factory() as session:
        repo = EmailRepository(session)
        row = repo.get_by_id(row_id)
        if row is None:
            return FetchEmailBodyResult(ok=False, row_id=str(row_id), reason="not_found")

        if row_has_cached_body(row):
            payload = json.loads(row.raw_payload_json or "{}")
            return FetchEmailBodyResult(
                ok=True,
                row_id=str(row_id),
                body_text=payload_body_text(payload),
                body_html=payload.get("body_html"),
                cached=True,
            )

        if not row.mailbox:
            return FetchEmailBodyResult(ok=False, row_id=str(row_id), reason="no_mailbox")

        imap_message_id = resolve_imap_message_id(row)
        try:
            body_text, body_html = fetch_message_from_imap(
                mailbox=row.mailbox,
                message_id=imap_message_id,
                vault=vault,
            )
        except EmailBodyNotFoundError:
            return FetchEmailBodyResult(
                ok=False,
                row_id=str(row_id),
                reason="not_in_mailbox",
            )
        except Exception as exc:
            return FetchEmailBodyResult(
                ok=False,
                row_id=str(row_id),
                reason=f"imap_error: {exc}",
            )

        if not (body_text or "").strip() and not body_html:
            return FetchEmailBodyResult(
                ok=False,
                row_id=str(row_id),
                reason="empty_body",
            )

        repo.cache_fetched_body(row_id, body_text=body_text, body_html=body_html)
        session.commit()
        return FetchEmailBodyResult(
            ok=True,
            row_id=str(row_id),
            body_text=body_text,
            body_html=body_html,
        )
