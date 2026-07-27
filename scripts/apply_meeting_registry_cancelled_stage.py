"""Добавляет значение cancelled в enum meetingregistrystage (PostgreSQL)."""

from __future__ import annotations

from sqlalchemy import create_engine, text

from app.core.config import settings

ENUM_QUERY = """
SELECT e.enumlabel
FROM pg_type t
JOIN pg_enum e ON t.oid = e.enumtypid
WHERE t.typname = 'meetingregistrystage'
ORDER BY e.enumsortorder
"""


def main() -> int:
    engine = create_engine(settings.DATABASE_URL_SYNC)
    with engine.connect() as conn:
        labels = [row[0] for row in conn.execute(text(ENUM_QUERY)).fetchall()]
        print(f"DB: {settings.POSTGRES_HOST}:{settings.POSTGRES_PORT}/{settings.POSTGRES_DB}")
        print(f"Current enum values: {labels}")

        if not labels:
            print("Enum meetingregistrystage not found — table may not be created yet.")
            return 1

        if "cancelled" in labels:
            print("Value 'cancelled' already exists — nothing to do.")
            return 0

        conn.execute(
            text("ALTER TYPE meetingregistrystage ADD VALUE IF NOT EXISTS 'cancelled'")
        )
        conn.commit()

        updated = [row[0] for row in conn.execute(text(ENUM_QUERY)).fetchall()]
        print(f"Updated enum values: {updated}")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
