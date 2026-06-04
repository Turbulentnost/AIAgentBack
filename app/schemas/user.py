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


class UserRead(ORMModel):
    id: uuid.UUID
    email: EmailStr
    username: str | None
    last_name: str | None
    first_name: str | None
    middle_name: str | None
    full_name: str | None
    phone: str | None
    position: str | None
    is_active: bool
    is_superuser: bool
    is_verified: bool
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
    email: EmailStr
    password: str
