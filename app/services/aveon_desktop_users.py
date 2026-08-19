"""Учётные записи Aveon desktop + dev bootstrap (единый источник правды)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password, verify_password
from app.models.user import User

DesktopRoleKind = Literal[
    "ceo",
    "leader",
    "deputy_leader",
    "manager",
    "production",
    "warehouse",
]


@dataclass(frozen=True)
class AveonDesktopUserSpec:
    email: str
    password: str
    full_name: str
    first_name: str
    last_name: str
    desktop_role: DesktopRoleKind
    is_superuser: bool = False


AVEON_DESKTOP_USERS: tuple[AveonDesktopUserSpec, ...] = (
    AveonDesktopUserSpec(
        email="temp.nd@local.dev",
        password="NdTemp2026!",
        full_name="Temp ND",
        first_name="Temp",
        last_name="ND",
        desktop_role="leader",
        is_superuser=True,
    ),
    AveonDesktopUserSpec(
        email="rodionov.pavel@local.dev",
        password="Rodionov2026!",
        full_name="Родионов Павел",
        first_name="Павел",
        last_name="Родионов",
        desktop_role="leader",
    ),
    AveonDesktopUserSpec(
        email="ermolenko.sergey@local.dev",
        password="Ermolenko2026!",
        full_name="Ермоленко Сергей Александрович",
        first_name="Сергей",
        last_name="Ермоленко",
        desktop_role="deputy_leader",
    ),
    AveonDesktopUserSpec(
        email="tishchenko.nadezhda@local.dev",
        password="Tishchenko2026!",
        full_name="Тищенко Надежда",
        first_name="Надежда",
        last_name="Тищенко",
        desktop_role="manager",
    ),
    AveonDesktopUserSpec(
        email="aksinin.leonid@local.dev",
        password="Aksinin2026!",
        full_name="Аксинин Леонид",
        first_name="Леонид",
        last_name="Аксинин",
        desktop_role="manager",
    ),
    AveonDesktopUserSpec(
        email="gaponova.ksenia@local.dev",
        password="Gaponova2026!",
        full_name="Гапонова Ксения Светославовна",
        first_name="Ксения",
        last_name="Гапонова",
        desktop_role="ceo",
    ),
    AveonDesktopUserSpec(
        email="bugata.pavel@local.dev",
        password="Bugata2026!",
        full_name="Бугата Павел Викторович",
        first_name="Павел",
        last_name="Бугата",
        desktop_role="production",
    ),
    AveonDesktopUserSpec(
        email="dogadin.alexandr@local.dev",
        password="Dogadin2026!",
        full_name="Догадин Александр Михайлович",
        first_name="Александр",
        last_name="Догадин",
        desktop_role="production",
    ),
    AveonDesktopUserSpec(
        email="kuraev.alexey@local.dev",
        password="Kuraev2026!",
        full_name="Кураев Алексей Витальевич",
        first_name="Алексей",
        last_name="Кураев",
        desktop_role="production",
    ),
    AveonDesktopUserSpec(
        email="golovinov.konstantin@local.dev",
        password="Golovinov2026!",
        full_name="Головинов Константин Эдуардович",
        first_name="Константин",
        last_name="Головинов",
        desktop_role="warehouse",
    ),
    AveonDesktopUserSpec(
        email="agadzhanyan.samvel@local.dev",
        password="Agadzhanyan2026!",
        full_name="Агаджанян Самвел Гагикович",
        first_name="Самвел",
        last_name="Агаджанян",
        desktop_role="warehouse",
    ),
)


def avion_platform_user_emails() -> frozenset[str]:
    return frozenset(
        spec.email.strip().casefold()
        for spec in AVEON_DESKTOP_USERS
        if not spec.is_superuser
    )


def avion_platform_user_full_names() -> frozenset[str]:
    return frozenset(spec.full_name.strip() for spec in AVEON_DESKTOP_USERS if not spec.is_superuser)


def is_registered_avion_platform_user(user: User | None) -> bool:
    if user is None or user.is_superuser:
        return False

    normalized_email = (user.email or "").strip().casefold()
    if normalized_email and normalized_email in avion_platform_user_emails():
        return True

    normalized_name = (user.full_name or "").strip()
    return normalized_name in avion_platform_user_full_names()


def _password_matches(user: User, password: str) -> bool:
    hashed = user.hashed_password or ""
    if not hashed:
        return False
    try:
        return verify_password(password, hashed)
    except Exception:
        return False


async def ensure_aveon_desktop_users(db: AsyncSession) -> list[str]:
    """Создаёт/обновляет пользователей desktop и выдаёт доступ к агенту Авион.

    Для bootstrap-аккаунтов @local.dev пароль всегда синхронизируется со спецификацией —
    иначе на чужой БД остаётся чужой hash и вход «верным» паролем даёт 401.
    """
    from app.services.document_analysis_permission import ensure_avion_only_user_agent_grant

    touched: list[str] = []

    for spec in AVEON_DESKTOP_USERS:
        email = spec.email.strip().lower()
        user = await db.scalar(select(User).where(User.email == email))
        if user is None:
            user = User(
                email=email,
                full_name=spec.full_name,
                first_name=spec.first_name,
                last_name=spec.last_name,
                hashed_password=hash_password(spec.password),
                is_active=True,
                is_verified=True,
                is_superuser=spec.is_superuser,
                must_change_password=False,
                deleted_at=None,
            )
            db.add(user)
            await db.flush()
            touched.append(f"{email}:created")
        else:
            user.email = email
            user.full_name = spec.full_name
            user.first_name = spec.first_name
            user.last_name = spec.last_name
            user.is_active = True
            user.is_verified = True
            user.must_change_password = False
            user.deleted_at = None
            if spec.is_superuser:
                user.is_superuser = True
            if not _password_matches(user, spec.password):
                user.hashed_password = hash_password(spec.password)
                touched.append(f"{email}:password_synced")
            else:
                touched.append(f"{email}:ok")

        if await ensure_avion_only_user_agent_grant(db, user):
            touched.append(f"{email}:grant")

    await db.commit()
    return touched


__all__ = [
    "AVEON_DESKTOP_USERS",
    "AveonDesktopUserSpec",
    "DesktopRoleKind",
    "avion_platform_user_emails",
    "avion_platform_user_full_names",
    "ensure_aveon_desktop_users",
    "is_registered_avion_platform_user",
]
