from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.models.user import Department, User, UserAgent
from app.schemas.department import DepartmentCreate, DepartmentUpdate
from app.schemas.user import AdminUserCreate, UserCreate, UserUpdate


class UserService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def list(self, limit: int = 50, offset: int = 0) -> list[User]:
        result = await self.db.execute(
            select(User)
            .where(User.deleted_at.is_(None))
            .order_by(User.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars().all())

    async def get(self, user_id: uuid.UUID) -> User | None:
        user = await self.db.get(User, user_id)
        if user is None or user.deleted_at is not None:
            return None
        return user

    async def get_by_email(self, email: str) -> User | None:
        result = await self.db.execute(select(User).where(User.email == email.lower()))
        return result.scalar_one_or_none()

    async def create(self, data: UserCreate, *, is_superuser: bool = False) -> User:
        values = data.model_dump(exclude={"password"})
        values["email"] = data.email.lower()
        values["hashed_password"] = hash_password(data.password)
        values["is_superuser"] = is_superuser
        values["full_name"] = values.get("full_name") or self._build_full_name(values)
        user = User(**values)
        self.db.add(user)
        await self.db.flush()
        return user

    async def create_by_admin(self, data: AdminUserCreate, *, created_by: uuid.UUID) -> User:
        existing_email = await self.get_by_email(data.email)
        if existing_email is not None:
            raise ValueError("Пользователь с таким email уже существует")
        if data.username:
            existing_username = await self.db.scalar(select(User).where(User.username == data.username))
            if existing_username is not None:
                raise ValueError("Пользователь с таким username уже существует")

        values = data.model_dump(exclude={"password", "agent_access"})
        values["email"] = data.email.lower()
        values["hashed_password"] = hash_password(data.password)
        values["full_name"] = values.get("full_name") or self._build_full_name(values)
        user = User(**values)
        self.db.add(user)
        await self.db.flush()

        for access in data.agent_access:
            self.db.add(
                UserAgent(
                    user_id=user.id,
                    agent_id=access.agent_id,
                    access_level=access.access_level,
                    can_run=access.can_run,
                    can_view_results=access.can_view_results,
                    can_approve=access.can_approve,
                    can_configure=access.can_configure,
                    granted_by=created_by,
                    expires_at=access.expires_at,
                )
            )
        await self.db.flush()
        return user

    async def update(self, user: User, data: UserUpdate) -> User:
        values = data.model_dump(exclude_unset=True)
        if "email" in values and values["email"] is not None:
            values["email"] = values["email"].lower()
        for key, value in values.items():
            setattr(user, key, value)
        if "full_name" not in values:
            rebuilt = self._build_full_name(
                {
                    "last_name": user.last_name,
                    "first_name": user.first_name,
                    "middle_name": user.middle_name,
                }
            )
            if rebuilt:
                user.full_name = rebuilt
        await self.db.flush()
        return user

    async def deactivate(self, user: User) -> User:
        user.is_active = False
        await self.db.flush()
        return user

    async def soft_delete(self, user: User) -> User:
        user.is_active = False
        user.deleted_at = datetime.now(timezone.utc)
        await self.db.flush()
        return user

    def _build_full_name(self, values: dict) -> str | None:
        parts = [values.get("last_name"), values.get("first_name"), values.get("middle_name")]
        return " ".join(part for part in parts if part) or None


class DepartmentService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def list(self, limit: int = 100, offset: int = 0) -> list[Department]:
        result = await self.db.execute(
            select(Department).order_by(Department.name).limit(limit).offset(offset)
        )
        return list(result.scalars().all())

    async def get(self, department_id: uuid.UUID) -> Department | None:
        return await self.db.get(Department, department_id)

    async def create(self, data: DepartmentCreate) -> Department:
        department = Department(**data.model_dump())
        self.db.add(department)
        await self.db.flush()
        return department

    async def update(self, department: Department, data: DepartmentUpdate) -> Department:
        for key, value in data.model_dump(exclude_unset=True).items():
            setattr(department, key, value)
        await self.db.flush()
        return department
