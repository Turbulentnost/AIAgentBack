"""Клиент OData 1С для синхронизации RAG и записи документов."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urljoin

import httpx


class ODataClient:
    """Минимальный OData v3/v4 клиент для публикаций 1С."""

    def __init__(
        self,
        base_url: str,
        *,
        username: str = "",
        password: str = "",
        timeout_sec: float = 60.0,
    ) -> None:
        self._base_url = base_url.rstrip("/") + "/"
        self._auth = (username, password) if username else None
        self._timeout = timeout_sec

    def fetch_all(self, entity: str, *, page_size: int = 500) -> list[dict[str, Any]]:
        """Читает все страницы сущности OData ($top / @odata.nextLink)."""
        entity = entity.strip("/")
        url: str | None = f"{self._base_url}{entity}?$format=json&$top={page_size}"
        rows: list[dict[str, Any]] = []

        with httpx.Client(timeout=self._timeout, auth=self._auth) as client:
            while url:
                response = client.get(url)
                response.raise_for_status()
                payload = response.json()
                batch = payload.get("value")
                if batch is None and isinstance(payload, list):
                    batch = payload
                if not isinstance(batch, list):
                    break
                rows.extend(item for item in batch if isinstance(item, dict))
                next_link = payload.get("@odata.nextLink") or payload.get("odata.nextLink")
                url = urljoin(self._base_url, next_link) if next_link else None
        return rows

    def create_entity(self, entity: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Создаёт запись сущности OData (POST). Возвращает тело ответа 1С."""
        entity = entity.strip("/")
        url = f"{self._base_url}{entity}?$format=json"
        with httpx.Client(timeout=self._timeout, auth=self._auth) as client:
            response = client.post(
                url,
                json=payload,
                headers={"Accept": "application/json", "Content-Type": "application/json"},
            )
            if response.status_code >= 400:
                try:
                    err_body = response.json()
                    odata_err = err_body.get("odata.error") if isinstance(err_body, dict) else None
                    if isinstance(odata_err, dict):
                        msg = (odata_err.get("message") or {}).get("value")
                        if msg:
                            raise ValueError(
                                f"OData POST {entity} failed ({response.status_code}): {msg}"
                            )
                except json.JSONDecodeError:
                    pass
            response.raise_for_status()
            data = response.json()
            if isinstance(data, dict):
                return data
            raise ValueError(f"Unexpected OData POST response type: {type(data)!r}")

    def get_by_key(self, entity: str, ref_key: str) -> dict[str, Any] | None:
        """Читает одну запись по Ref_Key. None, если 404."""
        entity = entity.strip("/")
        key = (ref_key or "").strip()
        if not key:
            return None
        url = f"{self._base_url}{entity}(guid'{key}')?$format=json"
        with httpx.Client(timeout=self._timeout, auth=self._auth) as client:
            response = client.get(url, headers={"Accept": "application/json"})
            if response.status_code == 404:
                return None
            if response.status_code >= 400:
                try:
                    err_body = response.json()
                    odata_err = err_body.get("odata.error") if isinstance(err_body, dict) else None
                    if isinstance(odata_err, dict):
                        msg = (odata_err.get("message") or {}).get("value")
                        if msg:
                            raise ValueError(
                                f"OData GET {entity}(guid'{key}') failed ({response.status_code}): {msg}"
                            )
                except json.JSONDecodeError:
                    pass
            response.raise_for_status()
            data = response.json()
            return data if isinstance(data, dict) else None

    def patch_entity(self, entity: str, ref_key: str, payload: dict[str, Any]) -> None:
        """Обновляет запись OData (PATCH). Для документов 1С — If-Match: *."""
        entity = entity.strip("/")
        key = (ref_key or "").strip()
        if not key:
            raise ValueError("ref_key is required for OData PATCH")
        url = f"{self._base_url}{entity}(guid'{key}')?$format=json"
        with httpx.Client(timeout=self._timeout, auth=self._auth) as client:
            response = client.patch(
                url,
                json=payload,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "If-Match": "*",
                },
            )
            if response.status_code >= 400:
                try:
                    err_body = response.json()
                    odata_err = err_body.get("odata.error") if isinstance(err_body, dict) else None
                    if isinstance(odata_err, dict):
                        msg = (odata_err.get("message") or {}).get("value")
                        if msg:
                            raise ValueError(
                                f"OData PATCH {entity}(guid'{key}') failed ({response.status_code}): {msg}"
                            )
                except json.JSONDecodeError:
                    pass
            response.raise_for_status()

    def put_entity_stream(
        self,
        entity: str,
        ref_key: str,
        stream_property: str,
        content: bytes,
        *,
        content_type: str = "application/octet-stream",
    ) -> None:
        """Записывает двоичные данные в Edm.Stream-свойство сущности OData (PUT)."""
        entity = entity.strip("/")
        stream_property = stream_property.strip()
        key = (ref_key or "").strip()
        if not key:
            raise ValueError("ref_key is required for OData stream PUT")
        if not content:
            raise ValueError("stream content is empty")
        url = f"{self._base_url}{entity}(guid'{key}')/{stream_property}"
        with httpx.Client(timeout=self._timeout, auth=self._auth) as client:
            response = client.put(
                url,
                content=content,
                headers={"Content-Type": content_type},
            )
            if response.status_code >= 400:
                try:
                    err_body = response.json()
                    odata_err = err_body.get("odata.error") if isinstance(err_body, dict) else None
                    if isinstance(odata_err, dict):
                        msg = (odata_err.get("message") or {}).get("value")
                        if msg:
                            raise ValueError(
                                f"OData PUT stream {entity}/{stream_property} failed "
                                f"({response.status_code}): {msg}"
                            )
                except json.JSONDecodeError:
                    pass
            response.raise_for_status()
