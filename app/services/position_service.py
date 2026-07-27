from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.position import DepartmentPosition, Position
from app.models.user import Department
from app.schemas.position import PositionDepartmentRead, PositionRead


class PositionService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def list(
        self,
        *,
        search: str | None = None,
        department_id: uuid.UUID | None = None,
        limit: int = 1000,
        active_only: bool = True,
        with_departments: bool = False,
    ) -> list[Position]:
        stmt = select(Position).order_by(Position.name.asc()).limit(limit)
        if active_only:
            stmt = stmt.where(Position.is_active.is_(True))
        if department_id is not None:
            stmt = stmt.join(DepartmentPosition).where(
                DepartmentPosition.department_id == department_id
            )
        if search:
            normalized = search.strip().lower().replace("ё", "е")
            if normalized:
                stmt = stmt.where(
                    Position.normalized_name.ilike(f"%{normalized}%")
                    | Position.name.ilike(f"%{normalized}%")
                )
        if with_departments:
            stmt = stmt.options(
                selectinload(Position.department_links).selectinload(DepartmentPosition.department)
            )
        result = await self.db.execute(stmt)
        return list(result.scalars().unique().all())

    async def get(self, position_id: uuid.UUID) -> Position | None:
        result = await self.db.execute(
            select(Position)
            .where(Position.id == position_id)
            .options(
                selectinload(Position.department_links).selectinload(DepartmentPosition.department)
            )
        )
        return result.scalar_one_or_none()

    def to_read(self, position: Position, *, with_departments: bool = True) -> PositionRead:
        departments: list[PositionDepartmentRead] = []
        if with_departments:
            for link in position.department_links:
                department = link.department
                if department is None:
                    continue
                departments.append(
                    PositionDepartmentRead(
                        id=department.id,
                        name=department.name,
                        slug=department.slug,
                    )
                )
            departments.sort(key=lambda item: item.name.casefold())
        return PositionRead(
            id=position.id,
            name=position.name,
            normalized_name=position.normalized_name,
            canonical_key=position.canonical_key,
            slug=position.slug,
            departments_count=position.departments_count,
            assignments_count=position.assignments_count,
            is_active=position.is_active,
            source_system=position.source_system,
            external_id=position.external_id,
            created_at=position.created_at,
            updated_at=position.updated_at,
            departments=departments,
        )
