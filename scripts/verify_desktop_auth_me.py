"""Проверка login + /auth/me на SQLite desktop (без PostgreSQL)."""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

sqlite_path = Path(os.environ["LOCALAPPDATA"]) / "AveonAgent" / "aveon_desktop_me_test.db"
if sqlite_path.exists():
    sqlite_path.unlink()

os.environ["DESKTOP_MODE"] = "1"
os.environ["DESKTOP_SQLITE_PATH"] = str(sqlite_path)
os.environ["AUTH_ALLOW_JWT_WITHOUT_SESSION"] = "true"
os.environ["ONEC_DAILY_SYNC_ENABLED"] = "false"
os.environ["ONEC_INPROCESS_SYNC_ENABLED"] = "false"

from app_desktop.bootstrap_env import load_desktop_env

load_desktop_env()
os.environ["DESKTOP_SQLITE_PATH"] = str(sqlite_path)
os.environ["AUTH_ALLOW_JWT_WITHOUT_SESSION"] = "true"

from app.core.config import settings
from app.services.auth_service import AuthService
from app.api.deps import authenticate_access_token
from app.api.v1.endpoints.auth import _user_read
from app_desktop.bootstrap_auth import bootstrap_desktop_auth_store
from app.db.session import AsyncSessionLocal


async def main() -> int:
    assert settings.is_sqlite
    await bootstrap_desktop_auth_store()
    async with AsyncSessionLocal() as db:
        user, token = await AuthService(db).authenticate(
            email="bugata.pavel@local.dev",
            password="Bugata2026!",
        )
        assert token is not None and user is not None
        await db.commit()

    async with AsyncSessionLocal() as db:
        user2 = await authenticate_access_token(db, token.access_token)
        profile = await _user_read(db, user2)
        print("ME OK", profile.email, profile.full_name)
        assert profile.email == "bugata.pavel@local.dev"
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
