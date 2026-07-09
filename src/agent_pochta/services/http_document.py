"""HTTP-адаптер Document Service (ТЗ §5.4)."""

from __future__ import annotations

import base64

import httpx

from agent_pochta.schemas import Attachment
from agent_pochta.services.document_service import DocumentService


class HttpDocumentService(DocumentService):
    def __init__(self, base_url: str, timeout_sec: float = 120.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_sec

    def extract(self, attachment: Attachment) -> Attachment:
        if attachment.content is None:
            return attachment
        payload = {
            "filename": attachment.filename,
            "mime_type": attachment.mime_type,
            "content_base64": base64.b64encode(attachment.content).decode("ascii"),
        }
        with httpx.Client(timeout=self._timeout) as client:
            response = client.post(f"{self._base_url}/v1/extract", json=payload)
            response.raise_for_status()
            data = response.json()
        attachment.extracted_text = data.get("extracted_text")
        attachment.ocr_used = bool(data.get("ocr_used"))
        return attachment
