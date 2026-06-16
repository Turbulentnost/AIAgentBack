from __future__ import annotations

import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.onec_odata import create_session
from app.models.user import User
from app.services import list_enterprise_positions as onec
from app.utils.department_classification import is_position_like_department_name, normalize_position_name
from app.utils.position_names import position_display_name


class PositionService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def list(self) -> list[str]:
        names: set[str] = set()

        result = await self.db.execute(
            select(User.position)
            .where(
                User.deleted_at.is_(None),
                User.position.is_not(None),
                User.position != "",
            )
            .distinct()
        )
        for position in result.scalars().all():
            normalized = normalize_position_name((position or "").strip())
            if normalized:
                names.add(normalized)

        try:
            session = await asyncio.to_thread(create_session)
            positions_map = await asyncio.to_thread(onec.load_positions, session)
            for position in positions_map.values():
                normalized = normalize_position_name((position or "").strip())
                if normalized:
                    names.add(normalized)

            structure_positions = await asyncio.to_thread(onec.build_enterprise_structure_positions, session)
            for row in structure_positions:
                normalized = position_display_name(external_id=row.get("external_id"), name=row["name"])
                if normalized:
                    names.add(normalized)
        except Exception:
            pass

        return sorted(names, key=str.casefold)
