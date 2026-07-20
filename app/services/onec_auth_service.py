from __future__ import annotations

import hashlib
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import create_access_token, hash_onec_password, hash_password, verify_onec_password
from app.integrations.onec_auth_api import OneCTokenPayload
from app.models.user import Role, User, UserSession
from app.schemas.user import Token
from app.services.audit_service import AuditService
from app.services.employee_sync_service import (
    SOURCE_SYSTEM,
    _parse_full_name,
    _sync_email,
    _sync_username,
)

DEFAULT_ROLE_CODE = "employee"


class OneCSessionExpiredError(RuntimeError):
    pass


class OneCSessionNotFoundError(RuntimeError):
    pass


@dataclass(frozen=True)
class OneCSessionData:
    token: str | None
    fio: str
    expires_at: datetime | None
    resolved_user: str | None
    resolved_user_source: str | None
    token_created_at: datetime
    reused: bool


class OneCAuthService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def login(
        self,
        *,
        fio: str,
        password: str,
        ip_address: str | None,
        user_agent: str | None,
    ) -> tuple[User, Token, OneCSessionData, bool]:
        display_fio = _normalize_fio(fio)
        now = datetime.now(timezone.utc)
        user = await self._find_user(display_fio, _external_id_from_fio(display_fio))
        created = user is None

        password_matches = (
            user is not None
            and user.onec_hashed_password is not None
            and verify_onec_password(password, user.onec_hashed_password)
        )

        if user is None:
            user, created = await self._get_or_create_user(display_fio)
        else:
            created = False

        if not password_matches:
            user.onec_hashed_password = hash_onec_password(password)
        user.last_login_at = now
        self._clear_onec_session(user)
        onec_session = self._build_session_data(user, display_fio, reused=False)
        await self.db.flush()

        platform_token = await self._issue_platform_session(
            user,
            created=created,
            reused_onec=onec_session.reused,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        return user, platform_token, onec_session, created

    async def complete_login(
        self,
        *,
        fio: str,
        password: str,
        token: str,
        expires_at: datetime | None,
        resolved_user: str | None,
        resolved_user_source: str | None,
        ip_address: str | None,
        user_agent: str | None,
    ) -> tuple[User, Token, bool]:
        display_name = _normalize_fio(resolved_user or fio)
        now = datetime.now(timezone.utc)
        user = await self._find_user(display_name, _external_id_from_fio(display_name))

        if user is not None and user.onec_hashed_password and not verify_onec_password(password, user.onec_hashed_password):
            self._clear_onec_session(user)

        onec_payload = OneCTokenPayload(
            token=token,
            expires_at=expires_at,
            resolved_user=resolved_user,
            resolved_user_source=resolved_user_source,
        )
        if user is None:
            user, created = await self._get_or_create_user(display_name)
        else:
            created = False
        self._update_user_profile(user, display_name, onec_payload)

        user.onec_hashed_password = hash_onec_password(password)
        self._apply_onec_token(user, onec_payload, now)
        platform_token = await self._issue_platform_session(
            user,
            created=created,
            reused_onec=False,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        return user, platform_token, created

    def get_session_for_user(self, user: User) -> OneCSessionData:
        now = datetime.now(timezone.utc)
        if not user.onec_access_token:
            raise OneCSessionNotFoundError("Сессия 1С не найдена")
        if self._is_onec_token_expired(user, now):
            self._clear_onec_session(user)
            raise OneCSessionExpiredError("Сессия 1С истекла. Войдите снова.")
        return self._build_session_data(user, user.full_name or "", reused=True)

    def ensure_onec_session_valid(self, user: User) -> None:
        if not user.onec_access_token:
            return
        now = datetime.now(timezone.utc)
        if self._is_onec_token_expired(user, now):
            self._clear_onec_session(user)
            raise OneCSessionExpiredError("Сессия 1С истекла. Войдите снова.")

    def clear_session(self, user: User) -> None:
        self._clear_onec_session(user)

    async def _get_or_create_user(self, display_name: str) -> tuple[User, bool]:
        external_id = _external_id_from_fio(display_name)
        existing = await self._find_user(display_name, external_id)
        if existing is not None:
            return existing, False

        names = _parse_full_name(display_name)
        employee_role = await self._get_employee_role()
        user = User(
            email=_sync_email(external_id),
            username=_sync_username(external_id, names),
            hashed_password=hash_password(secrets.token_urlsafe(24)),
            last_name=names["last_name"],
            first_name=names["first_name"],
            middle_name=names["middle_name"],
            full_name=display_name,
            source_system=SOURCE_SYSTEM,
            external_id=external_id,
            is_created_via_1c=True,
            is_active=True,
            is_verified=True,
            must_change_password=False,
            role_id=employee_role.id if employee_role else None,
        )
        self.db.add(user)
        try:
            await self.db.flush()
            return user, True
        except IntegrityError:
            await self.db.rollback()
            existing = await self._find_user(display_name, external_id)
            if existing is None:
                existing = await self._find_user_by_email(_sync_email(external_id))
            if existing is None:
                raise ValueError(
                    "Не удалось сохранить пользователя после входа через 1С. Повторите попытку."
                ) from None
            return existing, False

    def _update_user_profile(self, user: User, display_name: str, onec_payload: OneCTokenPayload) -> None:
        resolved = _normalize_fio(onec_payload.resolved_user or display_name)
        names = _parse_full_name(resolved)
        user.last_name = names["last_name"]
        user.first_name = names["first_name"]
        user.middle_name = names["middle_name"]
        user.full_name = resolved
        user.is_active = True
        user.deleted_at = None

    def _apply_onec_token(self, user: User, payload: OneCTokenPayload, now: datetime) -> None:
        user.onec_access_token = payload.token
        user.onec_token_expires_at = _aware(payload.expires_at) if payload.expires_at else None
        user.onec_token_created_at = now

    def _build_session_data(self, user: User, fio: str, *, reused: bool) -> OneCSessionData:
        return OneCSessionData(
            token=None,
            fio=fio,
            expires_at=None,
            resolved_user=user.full_name,
            resolved_user_source=None,
            token_created_at=datetime.now(timezone.utc),
            reused=reused,
        )

    def _clear_onec_session(self, user: User) -> None:
        user.onec_access_token = None
        user.onec_token_expires_at = None
        user.onec_token_created_at = None

    def _can_reuse_onec_token(self, user: User, now: datetime) -> bool:
        if not user.onec_access_token or not user.onec_token_created_at:
            return False
        return not self._is_onec_token_expired(user, now)

    def _is_onec_token_expired(self, user: User, now: datetime) -> bool:
        if not user.onec_token_created_at:
            return True
        max_age = timedelta(hours=settings.ONEC_TOKEN_MAX_AGE_HOURS)
        return now - user.onec_token_created_at >= max_age

    async def _issue_platform_session(
        self,
        user: User,
        *,
        created: bool,
        reused_onec: bool,
        ip_address: str | None,
        user_agent: str | None,
    ) -> Token:
        platform_expires_at = datetime.now(timezone.utc) + timedelta(
            minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES
        )
        token_id = uuid4().hex
        session = UserSession(
            user_id=user.id,
            token_jti=token_id,
            ip_address=ip_address,
            user_agent=user_agent,
            expires_at=platform_expires_at,
        )
        self.db.add(session)
        await AuditService(self.db).log(
            action="auth.onec_login",
            actor_id=user.id,
            resource_type="user_session",
            resource_id=token_id,
            payload={
                "created": created,
                "reused_onec_token": reused_onec,
                "is_created_via_1c": user.is_created_via_1c,
            },
            ip_address=ip_address,
            user_agent=user_agent,
        )
        return Token(
            access_token=create_access_token(user.id, token_id=token_id),
            expires_at=platform_expires_at,
        )

    async def _find_user(self, full_name: str, external_id: str) -> User | None:
        normalized = full_name.casefold()
        result = await self.db.execute(
            select(User).where(
                User.deleted_at.is_(None),
                func.lower(func.trim(User.full_name)) == normalized,
            )
        )
        user = result.scalar_one_or_none()
        if user is not None:
            return user

        result = await self.db.execute(
            select(User).where(
                User.deleted_at.is_(None),
                User.source_system == SOURCE_SYSTEM,
                User.external_id == external_id,
            )
        )
        user = result.scalar_one_or_none()
        if user is not None:
            return user

        return await self._find_user_by_email(_sync_email(external_id))

    async def _find_user_by_email(self, email: str) -> User | None:
        return await self.db.scalar(
            select(User).where(
                User.deleted_at.is_(None),
                User.email == email,
            )
        )

    async def _get_employee_role(self) -> Role | None:
        return await self.db.scalar(select(Role).where(Role.code == DEFAULT_ROLE_CODE))


def _normalize_fio(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip())


def _external_id_from_fio(fio: str) -> str:
    normalized = _normalize_fio(fio).casefold()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:32]


def _aware(value: datetime) -> datetime:
    if value.tzinfo is not None:
        return value
    return value.replace(tzinfo=timezone.utc)
