"""Разбор XML document (ТЗ §12) для API и UI."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from typing import Any

from agent_pochta.db.models import EmailMessageRow
from agent_pochta.routing.models import ConfidenceLevel, RoutingDecision, ServiceRoute
from agent_pochta.routing.xml_builder import (
    RESERVE_DEPARTMENT_CODE,
    SPAM_DEPARTMENT_CODE,
    build_xml_document,
    sanitize_theme,
    strip_forbidden_tags,
    validate_xml_document,
)
from agent_pochta.schemas import EmailMessage, SpamResult
from agent_pochta.state import AgentState

def _text(parent: ET.Element | None, tag: str) -> str:
    if parent is None:
        return ""
    node = parent.find(tag)
    if node is None or node.text is None:
        return ""
    return node.text.strip()


def _bool_text(value: str) -> bool:
    return value.strip().lower() == "true"


def parse_document_xml(xml: str) -> dict[str, Any] | None:
    """Парсит XML document в JSON-структуру для фронтенда."""
    if not xml or not isinstance(xml, str):
        return None
    try:
        root = ET.fromstring(xml.strip())
    except ET.ParseError:
        return None
    if root.tag != "document":
        return None

    services_node = root.find("services")
    services: list[dict[str, str]] = []
    if services_node is not None:
        for service in services_node.findall("service"):
            services.append(
                {
                    "name": _text(service, "name"),
                    "title": _text(service, "title"),
                    "process": _text(service, "process"),
                    "reasoning": _text(service, "reasoning"),
                }
            )

    return {
        "organization": _text(root, "organization"),
        "theme": _text(root, "theme"),
        "direction": _text(root, "направление"),
        "claim": _bool_text(_text(root, "claim")),
        "partner": _text(root, "partner"),
        "services": services,
        "email_sender": _text(root, "email_sender"),
        "email_recipient": _text(root, "email_recipient"),
        "mail_datetime": _text(root, "mail_datetime"),
        "process": _text(root, "process"),
        # Устаревшие поля — только для чтения старых XML
        "spam": _bool_text(_text(root, "spam")),
        "confidence_level": _text(root, "confidence_level"),
        "matching_keywords": _text(root, "matching_keywords"),
        "processing_notes": _text(root, "processing_notes"),
    }


def ensure_xml_document(state: AgentState) -> str | None:
    """Формирует XML document, если его ещё нет в meta (ранний спам и т.п.)."""
    meta = dict(state.get("meta") or {})
    existing = meta.get("xml_document")
    if isinstance(existing, str) and existing.strip():
        return existing

    email = state.get("email")
    if email is None:
        return None

    spam = state.get("spam")
    recipient = (
        meta.get("routing_recipient")
        or email.routing_recipient
        or email.mailbox
    )
    decision_meta = meta.get("routing_decision") or {}
    routing = state.get("routing")

    if routing is not None:
        direction = str(decision_meta.get("direction") or "КС")
        services = [
            ServiceRoute(
                code=routing.department_id,
                name=routing.department_name or routing.department_id,
                direction=direction,
            )
        ]
        try:
            level = ConfidenceLevel(decision_meta.get("confidence_level", ConfidenceLevel.LOW.value))
        except ValueError:
            level = ConfidenceLevel.LOW
        decision = RoutingDecision(
            organization=str(decision_meta.get("organization") or "НП"),
            direction=direction,
            services=services,
            confidence_level=level,
            confidence_score=int(decision_meta.get("confidence_score") or 0),
            partner=decision_meta.get("partner"),
            claim=bool(decision_meta.get("claim")),
            theme=sanitize_theme(email.subject or ""),
        )
    else:
        is_spam = bool(spam and spam.is_spam)
        services = [
            ServiceRoute(
                code=SPAM_DEPARTMENT_CODE if is_spam else RESERVE_DEPARTMENT_CODE,
                name="Спам" if is_spam else "Резерв",
                process="ознакомление" if is_spam else "исполнение",
            )
        ]
        decision = RoutingDecision(
            theme=sanitize_theme(email.subject or ""),
            services=services,
            confidence_level=ConfidenceLevel.LOW,
        )

    xml = build_xml_document(
        email,
        recipient=str(recipient),
        decision=decision,
        spam=spam if isinstance(spam, SpamResult) and spam.is_spam else None,
    )
    xml = strip_forbidden_tags(xml)
    if validate_xml_document(xml):
        return xml
    return None


def _payload_dict_from_row(row: EmailMessageRow) -> dict[str, Any]:
    if not row.raw_payload_json:
        return {}
    try:
        payload = json.loads(row.raw_payload_json)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def rebuild_xml_document_from_row(
    row: EmailMessageRow,
    email: EmailMessage,
    *,
    original_department_id: str | None = None,
    original_department_name: str | None = None,
    partner_override: str | None = None,
    process_override: str | None = None,
    organization_override: str | None = None,
) -> str | None:
    """Пересобирает XML document по текущим полям строки БД (human-in-the-loop)."""
    department_id = (row.department_id or "").strip()
    if not department_id:
        department_id = RESERVE_DEPARTMENT_CODE

    payload = _payload_dict_from_row(row)
    existing = parse_document_xml(str(payload.get("xml_document") or ""))

    previous_organization = (existing or {}).get("organization") or "НП"
    organization = organization_override or previous_organization or "НП"
    existing_direction = (existing or {}).get("direction") or "КС"
    if organization_override and organization_override != previous_organization:
        from agent_pochta.routing.organizations import direction_for_organization_override

        direction = direction_for_organization_override(
            organization,
            existing_direction=existing_direction,
            previous_organization=previous_organization,
        )
    else:
        direction = existing_direction
    partner = partner_override or (existing or {}).get("partner") or None
    claim = bool((existing or {}).get("claim"))

    department_name = row.department_name or department_id

    service_process = (
        process_override
        or (existing or {}).get("process")
        or ((existing or {}).get("services") or [{}])[0].get("process")
        or "исполнение"
    )

    decision = RoutingDecision(
        organization=organization,
        direction=direction,
        process=service_process,
        services=[
            ServiceRoute(
                code=department_id,
                name=department_name,
                process=service_process,
                direction=direction,
            )
        ],
        confidence_level=ConfidenceLevel.HIGH,
        confidence_score=100,
        partner=partner or None,
        claim=claim,
        theme=sanitize_theme((existing or {}).get("theme") or email.subject or ""),
    )

    recipient = (
        payload.get("routing_recipient")
        or email.routing_recipient
        or email.mailbox
    )
    xml = build_xml_document(
        email,
        recipient=str(recipient),
        decision=decision,
    )
    xml = strip_forbidden_tags(xml)
    if validate_xml_document(xml):
        return xml
    return None
