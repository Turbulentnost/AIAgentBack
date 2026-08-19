"""Проверка: после сброса hash пароль bugata снова работает через ensure + login."""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ["DESKTOP_MODE"] = "1"
os.environ["POSTGRES_HOST"] = "127.0.0.1"
os.environ["POSTGRES_PORT"] = "5432"
os.environ["POSTGRES_USER"] = "postgres"
os.environ["POSTGRES_PASSWORD"] = "postgres"
os.environ["POSTGRES_DB"] = "ai_agents"

from sqlalchemy import select

from app.core.security import hash_password, verify_password
from app.db.session import AsyncSessionLocal
from app.models.user import User
from app.services.auth_service import AuthService
from app.services.aveon_desktop_users import ensure_aveon_desktop_users


EMAIL = "bugata.pavel@local.dev"
PASSWORD = "Bugata2026!"


async def main() -> int:
    async with AsyncSessionLocal() as db:
        user = await db.scalar(select(User).where(User.email == EMAIL))
        if user is None:
            print("FAIL: user missing before test")
            return 1
        user.hashed_password = hash_password("WrongPassword999!")
        await db.commit()
        print("broken password set")

    async with AsyncSessionLocal() as db:
        ok_before = False
        user = await db.scalar(select(User).where(User.email == EMAIL))
        assert user is not None
        ok_before = verify_password(PASSWORD, user.hashed_password)
        print(f"verify before ensure: {ok_before}")
        if ok_before:
            print("FAIL: password should be broken")
            return 1

    async with AsyncSessionLocal() as db:
        touched = await ensure_aveon_desktop_users(db)
        print("ensure touched:", [item for item in touched if "bugata" in item])

    async with AsyncSessionLocal() as db:
        user = await db.scalar(select(User).where(User.email == EMAIL))
        assert user is not None
        ok_after = verify_password(PASSWORD, user.hashed_password)
        print(f"verify after ensure: {ok_after}")
        if not ok_after:
            print("FAIL: password not synced")
            return 1

        user_obj, token = await AuthService(db).authenticate(email=EMAIL, password=PASSWORD)
        if token is None or user_obj is None:
            print("FAIL: authenticate returned None")
            return 1
        print("authenticate OK")

    # Simulate login endpoint path with DESKTOP_MODE ensure-before-auth
    async with AsyncSessionLocal() as db:
        user = await db.scalar(select(User).where(User.email == EMAIL))
        assert user is not None
        user.hashed_password = hash_password("AnotherWrong111!")
        await db.commit()

    async with AsyncSessionLocal() as db:
        await ensure_aveon_desktop_users(db)
        user_obj, token = await AuthService(db).authenticate(email=EMAIL, password=PASSWORD)
        if token is None:
            print("FAIL: login-after-resync failed")
            return 1
        print("login-after-resync OK")

    print("ALL AUTH CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
