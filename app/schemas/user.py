from __future__ import annotations
import uuid
from datetime import datetime
from pydantic import BaseModel, EmailStr, Field
from app.schemas.common import ORMModel

class UserCreate(BaseModel):
    email: EmailStr
    username: str | None = Field(default=None, max_length=128)
    last_name: str | None = Field(default=None, max_length=128)
    first_name: str | None = Field(default=None, max_length=128)
    middle_name: str | None = Field(default=None, max_length=128)
    full_name: str | None = None
    phone: str | None = Field(default=None, max_length=64)
    position: str | None = Field(default=None, max_length=255)
    password: str = Field(..., min_length=8)
    department_id: uuid.UUID | None = None
    role_id: uuid.UUID | None = None


class UserAgentGrantCreate(BaseModel):
    agent_id: uuid.UUID
    access_level: str = Field(default="run", max_length=64)
    can_run: bool = True
    can_view_results: bool = True
    can_approve: bool = False
    can_configure: bool = False
    expires_at: datetime | None = None


class AdminUserCreate(UserCreate):
    is_active: bool = True
    is_verified: bool = True
    is_superuser: bool = False
    must_change_password: bool = True
    agent_access: list[UserAgentGrantCreate] = Field(default_factory=list)


class UserUpdate(BaseModel):
    email: EmailStr | None = None
    username: str | None = Field(default=None, max_length=128)
    last_name: str | None = Field(default=None, max_length=128)
    first_name: str | None = Field(default=None, max_length=128)
    middle_name: str | None = Field(default=None, max_length=128)
    full_name: str | None = None
    phone: str | None = Field(default=None, max_length=64)
    position: str | None = Field(default=None, max_length=255)
    department_id: uuid.UUID | None = None
    role_id: uuid.UUID | None = None
    is_active: bool | None = None
    is_verified: bool | None = None
    must_change_password: bool | None = None


class ResponsibleUserRead(ORMModel):
    id: uuid.UUID
    full_name: str | None
    position: str | None
    department_id: uuid.UUID | None = None
    department_name: str | None = None


class EmployeeSyncStatus(ORMModel):
    key: str
    source_system: str
    resource: str
    last_synced_at: datetime | None
    next_allowed_at: datetime | None
    status: str
    items_count: int
    error_message: str | None
    payload: dict | None = None


class EmployeeSyncResult(EmployeeSyncStatus):
    created_count: int = 0
    updated_count: int = 0
    deactivated_count: int = 0
    skipped_count: int = 0
    missing_department_count: int = 0
    synced_count: int = 0


class OneCLoginRequest(BaseModel):
    fio: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)


class OneCLoginCompleteRequest(BaseModel):
    fio: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)
    token: str = Field(..., min_length=1)
    expires_at: datetime | None = None
    resolved_user: str | None = None
    resolved_user_source: str | None = None


class OneCSessionRead(BaseModel):
    token: str | None = None
    fio: str
    expires_at: datetime | None = None
    resolved_user: str | None = None
    resolved_user_source: str | None = None
    token_created_at: datetime
    reused: bool = False


class OneCLoginResponse(BaseModel):
    access_token: str
    expires_at: datetime | None = None
    token_type: str = "bearer"
    user: "UserRead"
    is_created_via_1c: bool
    onec_session: OneCSessionRead
    token_reused: bool = False


class UserRead(ORMModel):
    id: uuid.UUID
    email: str
    username: str | None
    last_name: str | None
    first_name: str | None
    middle_name: str | None
    full_name: str | None
    phone: str | None
    position: str | None
    source_system: str | None = None
    external_id: str | None = None
    is_created_via_1c: bool = False
    has_onec_credentials: bool = False
    has_onec_session: bool = False
    is_active: bool
    is_superuser: bool
    is_verified: bool
    must_change_password: bool
    department_id: uuid.UUID | None
    role_id: uuid.UUID | None
    avatar_bucket: str | None = None
    avatar_object_name: str | None = None
    avatar_url: str | None = None
    last_login_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class Token(BaseModel):
    access_token: str
    expires_at: datetime | None = None
    token_type: str = "bearer"


class LoginRequest(BaseModel):
    email: str = Field(min_length=1, max_length=320)
    password: str
    new_password: str | None = None
