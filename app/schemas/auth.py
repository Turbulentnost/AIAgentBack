from __future__ import annotations

from app.schemas.user import LoginRequest, Token, UserCreate, UserRead

RegisterRequest = UserCreate

__all__ = ["LoginRequest", "RegisterRequest", "Token", "UserRead"]
