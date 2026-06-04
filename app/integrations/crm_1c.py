from __future__ import annotations
from typing import Any
from app.integrations.base import BaseConnector
class CRM1CConnector(BaseConnector):
    name = "crm_1c"
    async def fetch(self, resource: str, params: dict[str, Any] | None = None) -> Any:
        raise NotImplementedError("Интеграция с 1С:CRM подключается позже")
