from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import Role, User, user_roles

PSD_ASSISTANT_ROLE_CODE = "psd_assistant"
PSD_ASSISTANT_ROLE_NAME = "помощник ПСД"
PSD_CHAIRMAN_ASSISTANT_ROLE_CODE = "psd_chairman_assistant"
PSD_CHAIRMAN_ASSISTANT_ROLE_NAME = "помощник председателя совета директоров"
PSD_CHAIRMAN_ASSISTANT_ROLE_DISPLAY_NAME = "Помощник Председателя совета директоров"
PSD_DELEGATED_MANAGER_FIO = "Амураль Игорь Борисович"

# Явные code → ФИО (если роль в БД заведена с известным slug)
DELEGATED_MANAGER_ROLE_CODES: dict[str, str] = {
    PSD_ASSISTANT_ROLE_CODE: PSD_DELEGATED_MANAGER_FIO,
}

# Точное совпадение по Role.name (после нормализации)
DELEGATED_MANAGER_ROLE_NAMES: dict[str, str] = {
    PSD_ASSISTANT_ROLE_NAME: PSD_ASSISTANT_ROLE_CODE,
    PSD_CHAIRMAN_ASSISTANT_ROLE_NAME: "psd_chairman_assistant",
}

# Алиасы для обратной совместимости
PSD_ASSISTANT_MANAGER_FIO = PSD_DELEGATED_MANAGER_FIO
ROLE_MANAGER_FIO = DELEGATED_MANAGER_ROLE_CODES


def _normalized_role_label(value: str | None) -> str:
    if not isinstance(value, str):
        return ""
    return re.sub(r"\s+", " ", value.lower().replace("ё", "е")).strip()


def manager_fio_for_role_code(role_code: str) -> str | None:
    return DELEGATED_MANAGER_ROLE_CODES.get(role_code)


def match_delegated_manager_role(*, code: str | None, name: str | None) -> str | None:
    """
    Возвращает идентификатор делегированной роли для manager_fio_source,
    если поручения/протоколы нужно показывать от имени Амураля И.Б.
    """
    if code and code in DELEGATED_MANAGER_ROLE_CODES:
        return code

    normalized = _normalized_role_label(name)
    if not normalized:
        return None

    for role_name, role_key in DELEGATED_MANAGER_ROLE_NAMES.items():
        if normalized == _normalized_role_label(role_name):
            return role_key

    # UI может обрезать название: «Помощник Пред…»
    if normalized.startswith("помощник председателя"):
        return "psd_chairman_assistant"

    return None


def is_psd_assistant_role(*, code: str | None, name: str | None) -> bool:
    return match_delegated_manager_role(code=code, name=name) is not None


async def _load_user_roles(db: AsyncSession, user: User) -> list[tuple[str, str | None]]:
    roles: list[tuple[str, str | None]] = []

    if user.role_id is not None:
        primary = await db.get(Role, user.role_id)
        if primary is not None:
            roles.append((primary.code, primary.name))

    result = await db.execute(
        select(Role.code, Role.name)
        .join(user_roles, user_roles.c.role_id == Role.id)
        .where(user_roles.c.user_id == user.id)
    )
    roles.extend(result.all())

    position = (user.position or "").strip()
    if position:
        roles.append((None, position))

    return roles


def resolve_manager_fio_from_roles(roles: list[tuple[str, str | None]]) -> tuple[str, str] | None:
    for code, name in roles:
        matched = match_delegated_manager_role(code=code, name=name)
        if matched:
            return PSD_DELEGATED_MANAGER_FIO, f"role:{matched}"
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
    delegated = resolve_manager_fio_from_roles(roles)
    if delegated:
        return delegated

    full_name = (user.full_name or "").strip()
    if full_name:
        return full_name, "user_full_name"

    raise ValueError(
        "Не удалось определить ФИО руководителя: у пользователя нет подходящей роли "
        f"(например, «{PSD_ASSISTANT_ROLE_NAME}», «{PSD_CHAIRMAN_ASSISTANT_ROLE_NAME}») "
        "и не заполнено full_name"
    )
