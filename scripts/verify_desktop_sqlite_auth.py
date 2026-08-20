"""Проверка installer-only auth на SQLite без PostgreSQL."""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

sqlite_path = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "AveonAgent" / "aveon_desktop_test.db"
if sqlite_path.exists():
    sqlite_path.unlink()

os.environ["DESKTOP_MODE"] = "1"
os.environ["DESKTOP_SQLITE_PATH"] = str(sqlite_path)
os.environ["ONEC_DAILY_SYNC_ENABLED"] = "false"
os.environ["ONEC_INPROCESS_SYNC_ENABLED"] = "false"
os.environ.pop("POSTGRES_HOST", None)

from app_desktop.bootstrap_env import load_desktop_env

load_desktop_env()
os.environ["DESKTOP_SQLITE_PATH"] = str(sqlite_path)

from app.core.config import settings
from app.services.auth_service import AuthService
from app_desktop.bootstrap_auth import bootstrap_desktop_auth_store
from app.db.session import AsyncSessionLocal


async def main() -> int:
    assert settings.is_sqlite, settings.DATABASE_URL
    touched = await bootstrap_desktop_auth_store()
    print("ensured", len(touched), "sqlite=", sqlite_path)

    async with AsyncSessionLocal() as db:
        user, token = await AuthService(db).authenticate(
            email="bugata.pavel@local.dev",
            password="Bugata2026!",
        )
        if token is None or user is None:
            print("FAIL authenticate")
            return 1
        print("AUTH OK", user.email, user.full_name)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
