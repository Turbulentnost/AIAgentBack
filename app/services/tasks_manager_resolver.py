from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import Role, User, user_roles

PSD_ASSISTANT_ROLE_CODE = "psd_assistant"
PSD_ASSISTANT_ROLE_NAME = "помощник ПСД"
PSD_ASSISTANT_MANAGER_FIO = "Амураль Игорь Борисович"

ROLE_MANAGER_FIO: dict[str, str] = {
    PSD_ASSISTANT_ROLE_CODE: PSD_ASSISTANT_MANAGER_FIO,
}


def _normalized_role_label(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "").lower().replace("ё", "е")).strip()


def manager_fio_for_role_code(role_code: str) -> str | None:
    return ROLE_MANAGER_FIO.get(role_code)


def is_psd_assistant_role(*, code: str | None, name: str | None) -> bool:
    if code == PSD_ASSISTANT_ROLE_CODE:
        return True
    if not isinstance(name, str) or not name.strip():
        return False
    return _normalized_role_label(name) == _normalized_role_label(PSD_ASSISTANT_ROLE_NAME)


async def _load_user_roles(db: AsyncSession, user: User) -> list[tuple[str, str | None]]:
    roles: list[tuple[str, str | None]] = []

    if user.role_id is not None:
        primary = user.role
        if primary is None:
            primary = await db.get(Role, user.role_id)
        if primary is not None:
            roles.append((primary.code, primary.name))

    result = await db.execute(
        select(Role.code, Role.name)
        .join(user_roles, user_roles.c.role_id == Role.id)
        .where(user_roles.c.user_id == user.id)
    )
    roles.extend(result.all())
    return roles


def resolve_manager_fio_from_roles(roles: list[tuple[str, str | None]]) -> str | None:
    for code, name in roles:
        if is_psd_assistant_role(code=code, name=name):
            return manager_fio_for_role_code(PSD_ASSISTANT_ROLE_CODE) or PSD_ASSISTANT_MANAGER_FIO
        mapped = manager_fio_for_role_code(code)
        if mapped:
            return mapped
    return None


async def resolve_porucheniya_manager_fio(
    db: AsyncSession,
    user: User,
) -> tuple[str, str]:
    """
    Определяет ФИО руководителя для фильтра get_porucheniya.

    Возвращает (manager_fio, source), где source — откуда взято ФИО.
    """
    roles = await _load_user_roles(db, user)
    delegated_fio = resolve_manager_fio_from_roles(roles)
    if delegated_fio:
        return delegated_fio, f"role:{PSD_ASSISTANT_ROLE_CODE}"

    full_name = (user.full_name or "").strip()
    if full_name:
        return full_name, "user_full_name"

    raise ValueError(
        "Не удалось определить ФИО руководителя: у пользователя нет подходящей роли "
        f"(например, «{PSD_ASSISTANT_ROLE_NAME}») и не заполнено full_name"
    )
