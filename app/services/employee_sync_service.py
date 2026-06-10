from __future__ import annotations

import asyncio
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.security import hash_password
from app.integrations.onec_odata import create_session
from app.models.integration import IntegrationSyncState
from app.models.user import Department, Role, User
from app.services import list_enterprise_service as onec
from app.services.department_sync_service import DepartmentSyncService

SYNC_KEY = "1c.employees"
SOURCE_SYSTEM = "1c"
RESOURCE = "employees"
COOLDOWN = timedelta(days=1)
DEFAULT_ROLE_CODE = "employee"
SYNC_EMAIL_DOMAIN = "enterprise.sync.local"


class EmployeeSyncCooldownError(RuntimeError):
    def __init__(self, next_allowed_at: datetime) -> None:
        self.next_allowed_at = next_allowed_at
        super().__init__("Обновлять сотрудников из 1С можно не чаще одного раза в сутки")


class EmployeeSyncService:
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

    async def sync_from_1c(self, *, force: bool = False, sync_departments: bool = True) -> dict:
        state = await self.status()
        now = datetime.now(timezone.utc)
        if not force and state.next_allowed_at is not None and _aware(state.next_allowed_at) > now:
            raise EmployeeSyncCooldownError(_aware(state.next_allowed_at))

        state.status = "running"
        state.error_message = None
        await self.db.flush()

        try:
            if sync_departments:
                await DepartmentSyncService(self.db).sync_from_1c(force=force)

            rows = await asyncio.to_thread(self._fetch_assignments)
            result = await self._upsert_employees(rows)
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

    def _fetch_assignments(self) -> list[dict]:
        return onec.build_report(create_session())

    async def _upsert_employees(self, rows: list[dict]) -> dict:
        departments = await self._load_departments_by_path()
        employee_role = await self._get_employee_role()
        existing = await self._load_existing_synced_users()

        created = 0
        updated = 0
        skipped = 0
        missing_department = 0
        active_external_ids: set[str] = set()

        for row in rows:
            employee_name = (row.get("employee") or "").strip()
            external_id = (row.get("employee_key") or row.get("person_key") or "").strip()
            if not employee_name or not external_id:
                skipped += 1
                continue

            active_external_ids.add(external_id)
            department = departments.get((row.get("department") or "").strip())
            if department is None and row.get("department"):
                missing_department += 1

            names = _parse_full_name(employee_name)
            position = (row.get("position") or "").strip() or None
            user = existing.get(external_id)

            if user is None:
                email = _sync_email(external_id)
                user = User(
                    email=email,
                    username=_sync_username(external_id, names),
                    hashed_password=hash_password(secrets.token_urlsafe(24)),
                    last_name=names["last_name"],
                    first_name=names["first_name"],
                    middle_name=names["middle_name"],
                    full_name=employee_name,
                    position=position,
                    department_id=department.id if department else None,
                    role_id=employee_role.id if employee_role else None,
                    source_system=SOURCE_SYSTEM,
                    external_id=external_id,
                    is_active=True,
                    is_verified=False,
                    must_change_password=True,
                )
                self.db.add(user)
                existing[external_id] = user
                created += 1
            else:
                user.last_name = names["last_name"]
                user.first_name = names["first_name"]
                user.middle_name = names["middle_name"]
                user.full_name = employee_name
                user.position = position
                user.department_id = department.id if department else user.department_id
                if employee_role and user.role_id is None:
                    user.role_id = employee_role.id
                user.is_active = True
                user.deleted_at = None
                updated += 1

        await self.db.flush()

        deactivated = 0
        for external_id, user in existing.items():
            if external_id not in active_external_ids and user.is_active:
                user.is_active = False
                deactivated += 1

        await self.db.flush()
        return {
            "created_count": created,
            "updated_count": updated,
            "deactivated_count": deactivated,
            "skipped_count": skipped,
            "missing_department_count": missing_department,
            "synced_count": len(active_external_ids),
        }

    async def list_responsible_candidates(self, *, limit: int = 2000) -> list[User]:
        from app.services.user_service import UserService

        return await UserService(self.db).list_platform_access_users(limit=limit)

    async def _load_departments_by_path(self) -> dict[str, Department]:
        result = await self.db.execute(
            select(Department).where(
                Department.source_system == SOURCE_SYSTEM,
                Department.is_active.is_(True),
            )
        )
        by_path: dict[str, Department] = {}
        for department in result.scalars().all():
            if department.description:
                by_path[department.description.strip()] = department
            by_path[department.name.strip()] = department
        return by_path

    async def _load_existing_synced_users(self) -> dict[str, User]:
        result = await self.db.execute(select(User).where(User.source_system == SOURCE_SYSTEM))
        return {
            user.external_id: user
            for user in result.scalars().all()
            if user.external_id
        }

    async def _get_employee_role(self) -> Role | None:
        return await self.db.scalar(select(Role).where(Role.code == DEFAULT_ROLE_CODE))

    async def _get_state(self) -> IntegrationSyncState | None:
        return await self.db.scalar(select(IntegrationSyncState).where(IntegrationSyncState.key == SYNC_KEY))


def _parse_full_name(value: str) -> dict[str, str | None]:
    parts = value.split()
    if not parts:
        return {"last_name": None, "first_name": None, "middle_name": None}
    if len(parts) == 1:
        return {"last_name": parts[0], "first_name": None, "middle_name": None}
    if len(parts) == 2:
        return {"last_name": parts[0], "first_name": parts[1], "middle_name": None}
    return {
        "last_name": parts[0],
        "first_name": parts[1],
        "middle_name": " ".join(parts[2:]),
    }


def _sync_email(external_id: str) -> str:
    return f"1c+{external_id.lower()}@{SYNC_EMAIL_DOMAIN}"


def _sync_username(external_id: str, names: dict[str, str | None]) -> str:
    base = "-".join(
        part
        for part in (
            _slugify(names.get("last_name")),
            _slugify(names.get("first_name")),
        )
        if part
    )
    if not base:
        base = f"emp-{external_id[:8]}"
    suffix = external_id[:8].lower()
    candidate = f"{base[:96]}-{suffix}"
    return candidate[:128]


def _slugify(value: str | None) -> str:
    normalized = onec.normalize_text(value or "")
    slug = re.sub(r"[^a-zа-я0-9]+", "-", normalized, flags=re.IGNORECASE).strip("-")
    return slug


def _aware(value: datetime) -> datetime:
    if value.tzinfo is not None:
        return value
    return value.replace(tzinfo=timezone.utc)
