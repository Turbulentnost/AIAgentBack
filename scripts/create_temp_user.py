"""Создание временного пользователя для тестирования API.

Пример:
  python scripts/create_temp_user.py
  python scripts/create_temp_user.py --email temp.nd@local.dev --password "NdTemp2026!"
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_EMAIL = "temp.nd@local.dev"
DEFAULT_USERNAME = "temp_nd_admin"
DEFAULT_PASSWORD = "NdTemp2026!"
DEFAULT_FULL_NAME = "Временный администратор НД"


async def main() -> int:
    parser = argparse.ArgumentParser(description="Создать временного пользователя платформы")
    parser.add_argument("--email", default=DEFAULT_EMAIL)
    parser.add_argument("--username", default=DEFAULT_USERNAME)
    parser.add_argument("--password", default=DEFAULT_PASSWORD)
    parser.add_argument("--full-name", default=DEFAULT_FULL_NAME)
    parser.add_argument("--superuser", action="store_true", default=True)
    args = parser.parse_args()

    from app.db.session import AsyncSessionLocal
    from app.schemas.user import UserCreate
    from app.services.user_service import UserService
    from app.core.security import hash_password

    async with AsyncSessionLocal() as session:
        service = UserService(session)
        existing = await service.get_by_email(args.email)
        if existing is not None:
            existing.hashed_password = hash_password(args.password)
            existing.is_active = True
            existing.is_superuser = args.superuser
            existing.is_verified = True
            existing.must_change_password = False
            existing.full_name = args.full_name
            existing.username = args.username
            await session.commit()
            print("UPDATED")
            print(f"email={args.email}")
            print(f"username={args.username}")
            print(f"password={args.password}")
            print(f"user_id={existing.id}")
            return 0

        user = await service.create(
            UserCreate(
                email=args.email,
                username=args.username,
                password=args.password,
                full_name=args.full_name,
            ),
            is_superuser=args.superuser,
        )
        user.is_verified = True
        user.must_change_password = False
        await session.commit()
        print("CREATED")
        print(f"email={args.email}")
        print(f"username={args.username}")
        print(f"password={args.password}")
        print(f"user_id={user.id}")
        return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
