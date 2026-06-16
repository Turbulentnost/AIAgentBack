from __future__ import annotations

import asyncio
import re
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.onec_odata import create_session
from app.models.integration import IntegrationSyncState
from app.models.user import Department
from app.services import list_enterprise_positions as onec
from app.utils.department_classification import is_position_like_department_name
from app.utils.department_names import department_display_name

SYNC_KEY = "1c.departments"
SOURCE_SYSTEM = "1c"
RESOURCE = "departments"
COOLDOWN = timedelta(days=7)


class DepartmentSyncCooldownError(RuntimeError):
    def __init__(self, next_allowed_at: datetime) -> None:
        self.next_allowed_at = next_allowed_at
        super().__init__("Обновлять базу подразделений можно не чаще одного раза в неделю")


class DepartmentSyncService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def status(self) -> IntegrationSyncState:
        state = await self._get_state()
        if state is None:
            state = IntegrationSyncState(
                key=SYNC_KEY,
                source_system=SOURCE_SYSTEM,
                resource=RESOURCE,
                status="never",
                items_count=0,
            )
            self.db.add(state)
            await self.db.flush()
        return state

    async def sync_from_1c(self, *, force: bool = False) -> dict:
        state = await self.status()
        now = datetime.now(timezone.utc)
        if not force and state.next_allowed_at is not None and _aware(state.next_allowed_at) > now:
            raise DepartmentSyncCooldownError(_aware(state.next_allowed_at))

        state.status = "running"
        state.error_message = None
        await self.db.flush()

        try:
            rows = await asyncio.to_thread(self._fetch_departments)
            result = await self._upsert_departments(rows)
            state.last_synced_at = now
            state.next_allowed_at = now + COOLDOWN
            state.status = "success"
            state.items_count = result["synced_count"]
            state.payload = result
            await self.db.flush()
            return {
                **result,
                "last_synced_at": state.last_synced_at.isoformat(),
                "next_allowed_at": state.next_allowed_at.isoformat(),
            }
        except Exception as exc:
            state.status = "failed"
            state.error_message = str(exc)
            await self.db.flush()
            raise

    def _fetch_departments(self) -> list[dict]:
        return onec.build_enterprise_departments(create_session())

    async def _upsert_departments(self, rows: list[dict]) -> dict:
        result = await self.db.execute(select(Department).where(Department.source_system == SOURCE_SYSTEM))
        existing_by_external_id = {
            department.external_id: department
            for department in result.scalars().all()
            if department.external_id
        }
        all_result = await self.db.execute(select(Department))
        existing_by_slug = {department.slug: department for department in all_result.scalars().all()}

        by_external_id: dict[str, Department] = {}
        created = 0
        updated = 0

        for row in rows:
            external_id = str(row["external_id"])
            department = existing_by_external_id.get(external_id)
            display_name = department_display_name(external_id=external_id, name=row["name"])
            if department is None:
                slug = self._unique_slug(display_name, external_id, existing_by_slug)
                department = Department(
                    name=display_name,
                    slug=slug,
                    description=row["path"],
                    source_system=SOURCE_SYSTEM,
                    external_id=external_id,
                    is_active=True,
                )
                self.db.add(department)
                existing_by_slug[slug] = department
                created += 1
            else:
                department.name = display_name
                department.description = row["path"]
                department.is_active = True
                updated += 1
            by_external_id[external_id] = department

        await self.db.flush()

        for row in rows:
            department = by_external_id[row["external_id"]]
            parent_external_id = row.get("parent_external_id")
            parent = by_external_id.get(parent_external_id) if parent_external_id else None
            department.parent_id = parent.id if parent else None

        active_external_ids = {row["external_id"] for row in rows}
        deactivated = 0
        for external_id, department in existing_by_external_id.items():
            if external_id not in active_external_ids and department.is_active:
                department.is_active = False
                deactivated += 1

        reclassified = 0
        for department in existing_by_external_id.values():
            if department.is_active and is_position_like_department_name(department.name):
                department.is_active = False
                reclassified += 1

        await self.db.flush()
        return {
            "created_count": created,
            "updated_count": updated,
            "deactivated_count": deactivated,
            "reclassified_position_count": reclassified,
            "synced_count": len(rows),
        }

    def _unique_slug(
        self,
        name: str,
        external_id: str,
        existing_by_slug: dict[str, Department],
    ) -> str:
        base = _slugify(name)
        if not base:
            base = f"dept-{external_id[:8]}"
        candidate = base[:128]
        owner = existing_by_slug.get(candidate)
        if owner is None or owner.external_id == external_id:
            return candidate
        suffix = f"-{external_id[:8]}"
        return f"{base[:128 - len(suffix)]}{suffix}"

    async def _get_state(self) -> IntegrationSyncState | None:
        return await self.db.scalar(select(IntegrationSyncState).where(IntegrationSyncState.key == SYNC_KEY))


def _slugify(value: str) -> str:
    normalized = onec.normalize_text(value)
    slug = re.sub(r"[^a-zа-я0-9]+", "-", normalized, flags=re.IGNORECASE).strip("-")
    return slug[:128]


def _aware(value: datetime) -> datetime:
    if value.tzinfo is not None:
        return value
    return value.replace(tzinfo=timezone.utc)
