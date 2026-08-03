from __future__ import annotations

from typing import Any

from app.integrations.base import BaseConnector


class ERP1CConnector(BaseConnector):
    name = "erp_1c"

    async def fetch(self, resource: str, params: dict[str, Any] | None = None) -> Any:
        if resource == "nd_status_exchange":
            return {
                "mode": "file_exchange_stub",
                "resource": resource,
                "params": params or {},
                "note": "Для НД используйте /api/v1/nd-control/erp/status до подключения прямой интеграции",
            }
        raise NotImplementedError("Интеграция с 1С:ERP подключается позже")
