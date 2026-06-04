from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Table, Text, UniqueConstraint, Column
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


user_roles = Table(
    "user_roles",
    Base.metadata,
    Column("user_id", ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    Column("role_id", ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
)

role_permissions = Table(
    "role_permissions",
    Base.metadata,
    Column("role_id", ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
    Column("permission_id", ForeignKey("permissions.id", ondelete="CASCADE"), primary_key=True),
)


class Department(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "departments"

    name: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    slug: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    description: Mapped[str | None] = mapped_column(Text)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("departments.id", ondelete="SET NULL"),
        index=True,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    users: Mapped[list["User"]] = relationship(back_populates="department")
    agents: Mapped[list["DepartmentAgent"]] = relationship(
        back_populates="department",
        cascade="all, delete-orphan",
    )


class Permission(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "permissions"

    code: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    name: Mapped[str | None] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(String(255))

    roles: Mapped[list["Role"]] = relationship(secondary=role_permissions, back_populates="permissions")


class Role(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "roles"

    name: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    description: Mapped[str | None] = mapped_column(Text)
    is_system: Mapped[bool] = mapped_column(Boolean, default=False)

    primary_users: Mapped[list["User"]] = relationship(back_populates="role")
    users: Mapped[list["User"]] = relationship(secondary=user_roles, back_populates="roles")
    permissions: Mapped[list[Permission]] = relationship(
        secondary=role_permissions,
        back_populates="roles",
    )


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(128), unique=True, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255))
    last_name: Mapped[str | None] = mapped_column(String(128))
    first_name: Mapped[str | None] = mapped_column(String(128))
    middle_name: Mapped[str | None] = mapped_column(String(128))
    full_name: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(64))
    position: Mapped[str | None] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_superuser: Mapped[bool] = mapped_column(Boolean, default=False)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    department_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("departments.id", ondelete="SET NULL"),
        index=True,
    )
    role_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("roles.id", ondelete="SET NULL"),
        index=True,
    )
    avatar_bucket: Mapped[str | None] = mapped_column(String(255))
    avatar_object_name: Mapped[str | None] = mapped_column(String(1024))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)

    department: Mapped[Department | None] = relationship(back_populates="users")
    role: Mapped[Role | None] = relationship(back_populates="primary_users")
    roles: Mapped[list[Role]] = relationship(secondary=user_roles, back_populates="users")
    agent_access: Mapped[list["UserAgent"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        foreign_keys="UserAgent.user_id",
    )
    sessions: Mapped[list["UserSession"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )
    profile_images: Mapped[list["UserProfileImage"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )


class UserAgent(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "user_agents"
    __table_args__ = (UniqueConstraint("user_id", "agent_id", name="uq_user_agents_user_id_agent_id"),)

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), index=True)
    access_level: Mapped[str] = mapped_column(String(64), default="run")
    can_run: Mapped[bool] = mapped_column(Boolean, default=True)
    can_view_results: Mapped[bool] = mapped_column(Boolean, default=True)
    can_approve: Mapped[bool] = mapped_column(Boolean, default=False)
    can_configure: Mapped[bool] = mapped_column(Boolean, default=False)
    granted_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)

    user: Mapped[User] = relationship(back_populates="agent_access", foreign_keys=[user_id])
    agent: Mapped["Agent"] = relationship(back_populates="user_access")


class DepartmentAgent(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "department_agents"
    __table_args__ = (
        UniqueConstraint("department_id", "agent_id", name="uq_department_agents_department_id_agent_id"),
    )

    department_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("departments.id", ondelete="CASCADE"),
        index=True,
    )
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), index=True)
    access_level: Mapped[str] = mapped_column(String(64), default="run")
    can_run: Mapped[bool] = mapped_column(Boolean, default=True)
    can_view_results: Mapped[bool] = mapped_column(Boolean, default=True)
    can_approve: Mapped[bool] = mapped_column(Boolean, default=False)
    can_configure: Mapped[bool] = mapped_column(Boolean, default=False)

    department: Mapped[Department] = relationship(back_populates="agents")
    agent: Mapped["Agent"] = relationship(back_populates="department_access")


class UserSession(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "user_sessions"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_jti: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    ip_address: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(512))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)

    user: Mapped[User] = relationship(back_populates="sessions")


class UserProfileImage(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "user_profile_images"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    bucket: Mapped[str] = mapped_column(String(255))
    object_name: Mapped[str] = mapped_column(String(1024))
    size: Mapped[str] = mapped_column(String(32), default="original")
    content_type: Mapped[str | None] = mapped_column(String(128))
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)

    user: Mapped[User] = relationship(back_populates="profile_images")
