"""HTTP-клиент для внешних модулей, подключающихся к надстройке ЕСКД через API агента НД."""

from __future__ import annotations

from typing import Any

import httpx


class EskdApiClient:
    def __init__(self, *, base_url: str, access_token: str, timeout: float = 120.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.access_token = access_token
        self.timeout = timeout

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.access_token}"}

    async def get_module_info(self) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(f"{self.base_url}/nd-control/eskd/info", headers=self._headers())
            response.raise_for_status()
            return response.json()

    async def upload_and_register(
        self,
        *,
        file_path: str,
        designation: str | None = None,
        document_kind: str = "other",
        owner_department: str | None = None,
        start_processing: bool = True,
    ) -> dict[str, Any]:
        data: dict[str, str] = {
            "document_kind": document_kind,
            "start_processing": str(start_processing).lower(),
        }
        if designation:
            data["designation"] = designation
        if owner_department:
            data["owner_department"] = owner_department

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            with open(file_path, "rb") as handle:
                response = await client.post(
                    f"{self.base_url}/nd-control/eskd/documents/upload-register",
                    headers=self._headers(),
                    data=data,
                    files={"file": handle},
                )
            response.raise_for_status()
            return response.json()

    async def list_registrations(self, *, page: int = 1, size: int = 50) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(
                f"{self.base_url}/nd-control/eskd/registrations",
                headers=self._headers(),
                params={"page": page, "size": size},
            )
            response.raise_for_status()
            return response.json()

    async def validate_registration(self, registration_id: str) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.base_url}/nd-control/eskd/registrations/{registration_id}/validate",
                headers=self._headers(),
            )
            response.raise_for_status()
            return response.json()

    async def get_validation_report(self, registration_id: str) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(
                f"{self.base_url}/nd-control/eskd/registrations/{registration_id}/validation",
                headers=self._headers(),
            )
            response.raise_for_status()
            return response.json()
