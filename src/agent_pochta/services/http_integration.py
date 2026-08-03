"""HTTP-адаптер Integration Service → 1С:ERP (ТЗ §5.2)."""

from __future__ import annotations

import httpx

from agent_pochta.schemas import EmailMessage, RoutingResult
from agent_pochta.services.integration_service import IntegrationService


class HttpIntegrationService(IntegrationService):
    def __init__(self, base_url: str, timeout_sec: float = 60.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_sec

    def create_incoming_correspondence(
        self,
        email: EmailMessage,
        routing: RoutingResult,
        summary_ru: str,
        *,
        xml_document: str | None = None,
    ) -> dict:
        payload = {
            "received_at": email.received_at.isoformat(),
            "mailbox": email.mailbox,
            "sender_email": email.sender_email,
            "sender_name": email.sender_name,
            "subject": email.subject,
            "department_id": routing.department_id,
            "department_name": routing.department_name,
            "summary_ru": summary_ru,
            "priority": routing.priority.value,
            "source": "E-MAIL",
            "partner": "",
            "author": "ИИ-агент",
            "xml_document": xml_document,
        }
        with httpx.Client(timeout=self._timeout) as client:
            response = client.post(f"{self._base_url}/incoming-correspondence", json=payload)
            response.raise_for_status()
            data = response.json()
        return {
            "erp_document_number": data.get("erp_document_number") or data.get("document_number"),
            "erp_task_id": data.get("erp_task_id") or data.get("task_id"),
            "erp_document_id": data.get("erp_document_id") or data.get("document_id") or data.get("ref_key"),
            "fields": data.get("fields") or payload,
        }
