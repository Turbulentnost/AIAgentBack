from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import EskdUser

TEST_OTK_USERS = (
    {
        "login": "otk.ivanov",
        "display_name": "Иванов Иван Иванович",
        "role": "ESKD_OTK",
        "department": "ОТК",
    },
    {
        "login": "otk.petrova",
        "display_name": "Петрова Анна Сергеевна",
        "role": "ESKD_OTK",
        "department": "ОТК",
    },
)


@dataclass
class EskdActor:
    user_id: uuid.UUID | None
    login: str
    display_name: str
    role: str


class UserService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def ensure_seed_users(self) -> None:
        existing = {row.login for row in (await self._db.scalars(select(EskdUser))).all()}
        for row in TEST_OTK_USERS:
            if row["login"] in existing:
                continue
            self._db.add(EskdUser(**row, is_active=True))
        await self._db.commit()

    async def list_active(self, *, role: str | None = None) -> list[EskdUser]:
        query = select(EskdUser).where(EskdUser.is_active.is_(True)).order_by(EskdUser.display_name)
        if role:
            query = query.where(EskdUser.role == role)
        return list((await self._db.scalars(query)).all())

    async def get_by_login(self, login: str) -> EskdUser | None:
        value = login.strip()
        if not value:
            return None
        return await self._db.scalar(select(EskdUser).where(EskdUser.login == value))

    async def resolve_actor(self, login: str | None) -> EskdActor | None:
        if not login or not login.strip():
            return None
        user = await self.get_by_login(login.strip())
        if user:
            return EskdActor(
                user_id=user.id,
                login=user.login,
                display_name=user.display_name,
                role=user.role,
            )
        return EskdActor(
            user_id=None,
            login=login.strip(),
            display_name=login.strip(),
            role="ESKD_OTK",
        )
