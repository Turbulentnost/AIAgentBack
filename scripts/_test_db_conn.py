import asyncio

import asyncpg

from app.core.config import settings


async def main() -> None:
    conn = await asyncpg.connect(
        host=settings.POSTGRES_HOST,
        port=settings.POSTGRES_PORT,
        user=settings.POSTGRES_USER,
        password=settings.POSTGRES_PASSWORD,
        database=settings.POSTGRES_DB,
        timeout=15,
    )
    version = await conn.fetchval("select version()")
    tables = await conn.fetchval(
        """
        select count(*)
        from information_schema.tables
        where table_schema = 'public'
        """
    )
    print(f"OK host={settings.POSTGRES_HOST} db={settings.POSTGRES_DB} tables={tables}")
    print(version)
    await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
