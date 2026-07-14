"""Извлечение контрагентов из обработанных писем для Qdrant / erp_contractors."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from agent_pochta.routing.xml_parser import parse_document_xml
from agent_pochta.schemas import Contractor
from agent_pochta.services.llm_analyze import normalize_partner_name

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@dataclass(frozen=True)
class ExtractedContractor:
    email: str
    name: str
    contractor_id: str


def contractor_id_from_email(email: str) -> str:
    return f"email:{email.lower().strip()}"


def is_valid_sender_email(email: str | None) -> bool:
    value = (email or "").strip().lower()
    return bool(value and _EMAIL_RE.match(value))


def partner_from_payload(
    raw_payload_json: str | None,
    *,
    sender_name: str | None = None,
    sender_email: str | None = None,
    summary_ru: str | None = None,
) -> str | None:
    """Партнёр из xml_document; при ФИО в XML — повторное определение по домену/обзору."""
    from datetime import datetime, timezone

    from agent_pochta.schemas import EmailMessage
    from agent_pochta.services.llm_analyze import looks_like_org_name, resolve_partner_name

    body_text = ""
    xml_partner: str | None = None
    if raw_payload_json:
        try:
            payload = json.loads(raw_payload_json)
        except json.JSONDecodeError:
            payload = {}
        if isinstance(payload, dict):
            body_text = str(payload.get("body_text") or "")
            xml = payload.get("xml_document")
            if isinstance(xml, str) and xml.strip():
                parsed = parse_document_xml(xml)
                if parsed:
                    xml_partner = normalize_partner_name(parsed.get("partner"))

    if xml_partner and looks_like_org_name(xml_partner):
        return xml_partner

    email: EmailMessage | None = None
    if sender_email:
        email = EmailMessage(
            message_id="",
            mailbox="",
            sender_email=sender_email,
            sender_name=sender_name,
            body_text=body_text,
            received_at=datetime.now(timezone.utc),
        )

    resolved = resolve_partner_name(
        llm_partner=xml_partner if xml_partner and looks_like_org_name(xml_partner) else None,
        rag_partner=None,
        email=email,
        body_text=body_text or None,
        summary_ru=summary_ru,
    )
    if resolved:
        return resolved

    if sender_name and looks_like_org_name(sender_name):
        return normalize_partner_name(sender_name)
    return None


def extract_contractors_from_messages(
    rows: list[tuple[str, str | None, str | None]],
) -> list[ExtractedContractor]:
    """Дедупликация по sender_email; последнее письмо перезаписывает имя партнёра."""
    by_email: dict[str, ExtractedContractor] = {}
    skipped_invalid = 0

    for sender_email, sender_name, raw_payload_json in rows:
        email = (sender_email or "").strip().lower()
        if not is_valid_sender_email(email):
            skipped_invalid += 1
            continue

        name = partner_from_payload(
            raw_payload_json,
            sender_name=sender_name,
            sender_email=email,
        )
        if not name:
            skipped_invalid += 1
            continue

        by_email[email] = ExtractedContractor(
            email=email,
            name=name,
            contractor_id=contractor_id_from_email(email),
        )

    return list(by_email.values())


def to_contractor(record: ExtractedContractor) -> Contractor:
    return Contractor(
        contractor_id=record.contractor_id,
        name=record.name,
        emails=[record.email],
        department_codes=[],
        contractor_type="клиент",
    )
