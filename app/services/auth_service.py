from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import create_access_token, hash_password, verify_password
from app.models.user import User, UserSession
from app.schemas.user import Token, UserCreate
from app.services.audit_service import AuditService
from app.services.user_service import UserService


class AuthService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def register(self, data: UserCreate) -> User:
        user = await UserService(self.db).create(data)
        await AuditService(self.db).log(
            action="auth.register",
            actor_id=user.id,
            resource_type="user",
            resource_id=str(user.id),
        )
        return user

    async def authenticate(
        self,
        *,
        email: str,
        password: str,
        new_password: str | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> tuple[User | None, Token | None]:
        user = await UserService(self.db).get_by_email(email)
        if user is None or not user.is_active or not verify_password(password, user.hashed_password):
            await AuditService(self.db).log(
                action="auth.login_failed",
                actor_type="anonymous",
                payload={"email": email},
                ip_address=ip_address,
                user_agent=user_agent,
            )
            return None, None

        if user.must_change_password:
            if not new_password:
                await AuditService(self.db).log(
                    action="auth.password_change_required",
                    actor_id=user.id,
                    resource_type="user",
                    resource_id=str(user.id),
                    ip_address=ip_address,
                    user_agent=user_agent,
                )
                raise PasswordChangeRequired("Необходимо сменить временный пароль")
            if verify_password(new_password, user.hashed_password):
                raise ValueError("Новый пароль не должен совпадать с временным")
            user.hashed_password = hash_password(new_password)
            user.must_change_password = False
            user.is_verified = True
            await AuditService(self.db).log(
                action="auth.initial_password_changed",
                actor_id=user.id,
                resource_type="user",
                resource_id=str(user.id),
                ip_address=ip_address,
                user_agent=user_agent,
            )

        return user, await self.issue_session(
            user,
            action="auth.login",
            ip_address=ip_address,
            user_agent=user_agent,
        )

    async def issue_session(
        self,
        user: User,
        *,
        action: str = "auth.login",
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> Token:
        expires_at = datetime.now(timezone.utc) + timedelta(
            minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES
        )
        token_id = uuid4().hex
        user.last_login_at = datetime.now(timezone.utc)
        session = UserSession(
            user_id=user.id,
            token_jti=token_id,
            ip_address=ip_address,
            user_agent=user_agent,
            expires_at=expires_at,
        )
        self.db.add(session)
        await AuditService(self.db).log(
            action=action,
            actor_id=user.id,
            resource_type="user_session",
            resource_id=token_id,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        return Token(
            access_token=create_access_token(user.id, token_id=token_id),
            expires_at=expires_at,
        )

    async def logout(
        self,
        *,
        user: User,
        token_id: str | None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        if token_id:
            session = await self.db.scalar(select(UserSession).where(UserSession.token_jti == token_id))
            if session is not None:
                session.revoked_at = datetime.now(timezone.utc)
        await AuditService(self.db).log(
            action="auth.logout",
            actor_id=user.id,
            resource_type="user",
            resource_id=str(user.id),
            ip_address=ip_address,
            user_agent=user_agent,
        )


class PasswordChangeRequired(RuntimeError):
    pass
