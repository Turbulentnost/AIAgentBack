"""Сопоставление routing_corrections с письмами в БД и загрузка тела."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from agent_pochta.db.message_filters import load_payload_dict, recipient_display_value
from agent_pochta.db.models import EmailMessageRow
from agent_pochta.routing.corrections import _clean_token, _strip_subject_prefix, load_corrections
from agent_pochta.routing.normalize import normalize_email_address, normalize_text
from agent_pochta.services.routing_departments import load_routing_rules


class MatchStatus(str, Enum):
    UNAMBIGUOUS = "unambiguous"
    AMBIGUOUS = "ambiguous"
    NOT_FOUND = "not_found"
    SKIPPED = "skipped"


class BodySource(str, Enum):
    DB = "body_from_db"
    IMAP = "body_from_imap"
    MISSING = "body_missing"


@dataclass
class CorrectionMatch:
    corr_id: str
    status: MatchStatus
    email_id: str | None
    sender_email: str
    recipient: str | None
    subject: str
    department_id: str
    department_name: str
    original_department_id: str | None
    original_department_name: str | None
    body: str
    body_source: BodySource
    candidate_count: int
    created_at: str
    keywords: list[str]


def _parse_created_at(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def _normalize_subject(subject: str | None) -> str:
    cleaned = _strip_subject_prefix(subject or "")
    return normalize_text(_clean_token(cleaned))


def _row_recipient_normalized(row: EmailMessageRow, aliases: dict[str, str]) -> str:
    payload = load_payload_dict(row.raw_payload_json) or {}
    displayed = recipient_display_value(mailbox=row.mailbox, payload=payload) or row.mailbox
    return normalize_email_address(displayed, aliases).lower()


def _subject_matches(expected: str, actual: str) -> bool:
    if not expected:
        return True
    if not actual:
        return False
    if expected == actual:
        return True
    return expected in actual or actual in expected


def _recipient_matches(expected: str | None, row_recipient: str) -> bool:
    if not expected:
        return True
    expected = expected.lower()
    row_recipient = row_recipient.lower()
    if expected == row_recipient:
        return True
    exp_local = expected.split("@", 1)[0] if "@" in expected else expected
    row_local = row_recipient.split("@", 1)[0] if "@" in row_recipient else row_recipient
    return exp_local == row_local or exp_local in row_recipient


def find_email_candidates(
    session: Session,
    entry: dict[str, Any],
    *,
    window_days: int = 7,
) -> list[EmailMessageRow]:
    rules = load_routing_rules()
    aliases = rules.get("email_aliases") or {}

    sender = str(entry.get("sender_email") or "").lower().strip()
    recipient = normalize_email_address(str(entry.get("recipient") or ""), aliases).lower()
    subject_norm = str(entry.get("subject") or "").strip().lower()
    if not subject_norm:
        subject_norm = _normalize_subject(str(entry.get("keywords", [""])[0] if entry.get("keywords") else ""))

    created = _parse_created_at(str(entry.get("created_at") or ""))
    if not sender:
        return []

    stmt = select(EmailMessageRow).where(
        EmailMessageRow.sender_email.ilike(sender),
    )
    if created:
        start = created - timedelta(days=window_days)
        end = created + timedelta(days=1)
        stmt = stmt.where(
            EmailMessageRow.received_at >= start,
            EmailMessageRow.received_at <= end,
        )

    rows = list(session.scalars(stmt).all())
    candidates: list[EmailMessageRow] = []
    for row in rows:
        row_subject = _normalize_subject(row.subject)
        row_recipient = _row_recipient_normalized(row, aliases)
        if not _recipient_matches(recipient or None, row_recipient):
            continue
        if subject_norm and not _subject_matches(subject_norm, row_subject):
            continue
        candidates.append(row)

    if not candidates and created:
        # Wider window fallback
        stmt2 = select(EmailMessageRow).where(EmailMessageRow.sender_email.ilike(sender))
        rows2 = list(session.scalars(stmt2).all())
        for row in rows2:
            row_subject = _normalize_subject(row.subject)
            row_recipient = _row_recipient_normalized(row, aliases)
            if not _recipient_matches(recipient or None, row_recipient):
                continue
            if subject_norm and not _subject_matches(subject_norm, row_subject):
                continue
            candidates.append(row)

    return candidates


def _body_from_row(row: EmailMessageRow) -> str:
    payload = load_payload_dict(row.raw_payload_json) or {}
    return str(payload.get("body_text") or payload.get("body") or "").strip()


def fetch_body_via_api(
    email_id: uuid.UUID,
    *,
    api_base: str = "http://127.0.0.1:8080",
    timeout_sec: float = 60.0,
) -> tuple[str, BodySource]:
    url = f"{api_base.rstrip('/')}/api/v1/email-messages/{email_id}/fetch-body"
    try:
        with httpx.Client(timeout=timeout_sec) as client:
            resp = client.post(url)
        if resp.status_code >= 400:
            return "", BodySource.MISSING
        data = resp.json()
        body = str(data.get("body_text") or "").strip()
        return body, (BodySource.IMAP if body else BodySource.MISSING)
    except Exception:
        return "", BodySource.MISSING


def resolve_email_body(
    row: EmailMessageRow,
    *,
    api_base: str = "http://127.0.0.1:8080",
    fetch_imap: bool = True,
) -> tuple[str, BodySource]:
    body = _body_from_row(row)
    if body:
        return body, BodySource.DB
    if fetch_imap:
        body, source = fetch_body_via_api(row.id, api_base=api_base)
        if body:
            return body, source
    return "", BodySource.MISSING


def match_correction_entry(
    session: Session,
    entry: dict[str, Any],
    *,
    window_days: int = 7,
    api_base: str = "http://127.0.0.1:8080",
    fetch_imap: bool = True,
) -> CorrectionMatch:
    corr_id = str(entry.get("id") or "")
    subject = str(entry.get("subject") or "")
    if not subject.strip():
        kw = entry.get("keywords") or []
        if kw:
            subject = str(kw[0])

    if not subject.strip():
        return CorrectionMatch(
            corr_id=corr_id,
            status=MatchStatus.SKIPPED,
            email_id=None,
            sender_email=str(entry.get("sender_email") or ""),
            recipient=entry.get("recipient"),
            subject="",
            department_id=str(entry.get("department_id") or ""),
            department_name=str(entry.get("department_name") or ""),
            original_department_id=entry.get("original_department_id"),
            original_department_name=entry.get("original_department_name"),
            body="",
            body_source=BodySource.MISSING,
            candidate_count=0,
            created_at=str(entry.get("created_at") or ""),
            keywords=list(entry.get("keywords") or []),
        )

    candidates = find_email_candidates(session, entry, window_days=window_days)
    if len(candidates) == 0:
        status = MatchStatus.NOT_FOUND
        row = None
    elif len(candidates) == 1:
        status = MatchStatus.UNAMBIGUOUS
        row = candidates[0]
    else:
        status = MatchStatus.AMBIGUOUS
        row = None

    body = ""
    body_source = BodySource.MISSING
    email_id: str | None = None
    if row is not None:
        email_id = str(row.id)
        body, body_source = resolve_email_body(row, api_base=api_base, fetch_imap=fetch_imap)

    return CorrectionMatch(
        corr_id=corr_id,
        status=status,
        email_id=email_id,
        sender_email=str(entry.get("sender_email") or ""),
        recipient=entry.get("recipient"),
        subject=subject,
        department_id=str(entry.get("department_id") or ""),
        department_name=str(entry.get("department_name") or ""),
        original_department_id=entry.get("original_department_id"),
        original_department_name=entry.get("original_department_name"),
        body=body,
        body_source=body_source,
        candidate_count=len(candidates),
        created_at=str(entry.get("created_at") or ""),
        keywords=list(entry.get("keywords") or []),
    )


def match_all_corrections(
    session: Session,
    *,
    path: str | None = None,
    window_days: int = 7,
    api_base: str = "http://127.0.0.1:8080",
    fetch_imap: bool = True,
    only_unambiguous: bool = False,
) -> list[CorrectionMatch]:
    store = load_corrections(path)
    results: list[CorrectionMatch] = []
    for entry in store.get("entries") or []:
        match = match_correction_entry(
            session,
            entry,
            window_days=window_days,
            api_base=api_base,
            fetch_imap=fetch_imap,
        )
        if only_unambiguous and match.status != MatchStatus.UNAMBIGUOUS:
            continue
        results.append(match)
    return results
