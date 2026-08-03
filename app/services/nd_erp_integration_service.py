from __future__ import annotations

import uuid
from typing import Any


class NdErpIntegrationService:
    """Интеграция НД с 1С ERP (ТЗ п. 6.1) — файловый/API контур."""

    async def push_document_status(
        self,
        *,
        document_code: str,
        status: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "ok": True,
            "mode": "file_exchange_stub",
            "document_code": document_code,
            "status": status,
            "metadata": metadata or {},
            "note": "Прямая интеграция 1С ERP будет подключена на этапе промышленного внедрения",
        }

    async def pull_org_structure(self) -> dict[str, Any]:
        return {"departments": [], "positions": [], "mode": "stub"}

    async def attach_files_to_service_memo(
        self,
        *,
        memo_number: str,
        file_ids: list[uuid.UUID],
    ) -> dict[str, Any]:
        return {
            "ok": True,
            "memo_number": memo_number,
            "attached_files": [str(file_id) for file_id in file_ids],
            "mode": "stub",
        }
