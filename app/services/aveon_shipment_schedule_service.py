from __future__ import annotations

import base64
import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.document_analysis_agent.temp_schedule_merge import (
    build_merged_schedule_preview_values,
    merge_schedule_files,
)
from app.models.aveon_shipment_schedule import (
    AveonShipmentScheduleChangeEvent,
    AveonShipmentScheduleVersion,
)

RUSSIA_SCOPE = "russia"


class AveonShipmentScheduleError(ValueError):
    pass


@dataclass(frozen=True)
class ActiveShipmentSchedule:
    version: AveonShipmentScheduleVersion
    raw: bytes


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _encode(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _decode(value: str) -> bytes:
    return base64.b64decode(value)


def shipment_change_idempotency_key(
    *,
    task_key: str | None,
    manager_result: str,
    active_version_id: uuid.UUID | str | None,
    nomenclature: str,
) -> str:
    seed = "|".join(
        [
            str(task_key or ""),
            str(active_version_id or ""),
            nomenclature.strip().casefold(),
            hashlib.sha256(manager_result.strip().encode("utf-8")).hexdigest(),
        ]
    )
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


class AveonShipmentScheduleService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get_active_russia(self) -> ActiveShipmentSchedule | None:
        result = await self.db.execute(
            select(AveonShipmentScheduleVersion)
            .where(
                AveonShipmentScheduleVersion.country_scope == RUSSIA_SCOPE,
                AveonShipmentScheduleVersion.is_active.is_(True),
            )
            .order_by(AveonShipmentScheduleVersion.created_at.desc())
            .limit(1)
        )
        version = result.scalar_one_or_none()
        if version is None:
            return None
        return ActiveShipmentSchedule(version=version, raw=_decode(version.file_base64))

    async def list_russia_versions(self, *, limit: int = 20) -> list[AveonShipmentScheduleVersion]:
        result = await self.db.execute(
            select(AveonShipmentScheduleVersion)
            .where(AveonShipmentScheduleVersion.country_scope == RUSSIA_SCOPE)
            .order_by(AveonShipmentScheduleVersion.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def save_russia_upload(
        self,
        *,
        filename: str,
        raw: bytes,
        created_by_user_id: uuid.UUID | None,
        reason: str = "admin_upload",
    ) -> AveonShipmentScheduleVersion:
        if not raw:
            raise AveonShipmentScheduleError("Файл графика пуст")

        merge_result = await merge_schedule_files(
            [(filename, raw)],
            include_google_sheets=False,
            include_merged_inputs=True,
        )
        if not merge_result.get("ok") or not merge_result.get("file_base64"):
            raise AveonShipmentScheduleError(
                str(merge_result.get("message") or "Не удалось разобрать российский график отгрузок")
            )

        canonical_raw = _decode(str(merge_result["file_base64"]))
        return await self.save_russia_version(
            filename=str(merge_result.get("file_name") or "russia_shipment_schedule.xlsx"),
            raw=canonical_raw,
            created_by_user_id=created_by_user_id,
            reason=reason,
            preview_values=merge_result.get("preview_values") or [],
            stats=merge_result.get("stats") or {},
            changed_cells=merge_result.get("changed_cells") or [],
            source_type="admin_upload",
        )

    async def save_russia_version(
        self,
        *,
        filename: str,
        raw: bytes,
        created_by_user_id: uuid.UUID | None,
        reason: str,
        preview_values: list[list[str]] | None = None,
        stats: dict[str, Any] | None = None,
        changed_cells: list[dict[str, int]] | None = None,
        source_type: str = "manager_result",
    ) -> AveonShipmentScheduleVersion:
        file_hash = _sha256(raw)
        result = await self.db.execute(
            select(AveonShipmentScheduleVersion).where(
                AveonShipmentScheduleVersion.country_scope == RUSSIA_SCOPE,
                AveonShipmentScheduleVersion.file_sha256 == file_hash,
            )
        )
        version = result.scalar_one_or_none()

        await self._deactivate_russia_versions()
        if version is None:
            preview = preview_values
            if preview is None:
                preview = build_merged_schedule_preview_values(raw)
            version = AveonShipmentScheduleVersion(
                country_scope=RUSSIA_SCOPE,
                source_type=source_type,
                file_name=filename,
                file_sha256=file_hash,
                file_base64=_encode(raw),
                preview_json=preview,
                stats_json=stats or {},
                changed_cells_json=changed_cells or [],
                created_by_user_id=created_by_user_id,
                created_reason=reason,
                is_active=True,
            )
            self.db.add(version)
        else:
            version.source_type = source_type
            version.file_name = filename
            version.preview_json = preview_values if preview_values is not None else version.preview_json
            version.stats_json = stats or version.stats_json or {}
            version.changed_cells_json = changed_cells or version.changed_cells_json or []
            version.created_reason = reason
            version.created_by_user_id = created_by_user_id
            version.is_active = True
        await self.db.flush()
        return version

    async def _deactivate_russia_versions(self) -> None:
        result = await self.db.execute(
            select(AveonShipmentScheduleVersion).where(
                AveonShipmentScheduleVersion.country_scope == RUSSIA_SCOPE,
                AveonShipmentScheduleVersion.is_active.is_(True),
            )
        )
        for version in result.scalars().all():
            version.is_active = False

    async def get_change_event_by_key(self, idempotency_key: str) -> AveonShipmentScheduleChangeEvent | None:
        result = await self.db.execute(
            select(AveonShipmentScheduleChangeEvent).where(
                AveonShipmentScheduleChangeEvent.idempotency_key == idempotency_key
            )
        )
        return result.scalar_one_or_none()

    async def record_change_event(
        self,
        *,
        idempotency_key: str,
        status: str,
        nomenclature: str,
        country: str | None,
        message: str | None,
        manager_user_id: uuid.UUID | None = None,
        manager_name: str | None = None,
        task_key: str | None = None,
        task_type: str | None = None,
        schedule_version_id: uuid.UUID | None = None,
        next_schedule_version_id: uuid.UUID | None = None,
        supplier: str | None = None,
        original_dates: list[str] | None = None,
        add_batches: list[dict[str, Any]] | None = None,
        quantity: float | None = None,
        manager_result: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> AveonShipmentScheduleChangeEvent:
        existing = await self.get_change_event_by_key(idempotency_key)
        if existing is not None:
            return existing
        event = AveonShipmentScheduleChangeEvent(
            idempotency_key=idempotency_key,
            status=status,
            schedule_version_id=schedule_version_id,
            next_schedule_version_id=next_schedule_version_id,
            manager_user_id=manager_user_id,
            manager_name=manager_name,
            task_key=task_key,
            task_type=task_type,
            nomenclature=nomenclature,
            country=country,
            supplier=supplier,
            original_dates_json=original_dates or [],
            add_batches_json=add_batches or [],
            quantity=quantity,
            manager_result=manager_result,
            message=message,
            event_metadata=metadata or {},
            applied_at=datetime.now(timezone.utc),
        )
        self.db.add(event)
        await self.db.flush()
        return event

    @staticmethod
    def serialize_version(version: AveonShipmentScheduleVersion | None) -> dict[str, Any] | None:
        if version is None:
            return None
        return {
            "id": str(version.id),
            "country_scope": version.country_scope,
            "source_type": version.source_type,
            "file_name": version.file_name,
            "file_sha256": version.file_sha256,
            "preview_values": version.preview_json or [],
            "stats": version.stats_json or {},
            "changed_cells": version.changed_cells_json or [],
            "is_active": version.is_active,
            "created_reason": version.created_reason,
            "created_at": version.created_at.isoformat() if version.created_at else None,
            "updated_at": version.updated_at.isoformat() if version.updated_at else None,
        }
