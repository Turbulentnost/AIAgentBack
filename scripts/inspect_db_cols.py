import asyncio
from sqlalchemy import text
from app.db.session import AsyncSessionLocal

async def main():
    async with AsyncSessionLocal() as db:
        r = await db.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='nd_structural_document_cards' ORDER BY 1"
        ))
        print([row[0] for row in r.fetchall()])
        v = await db.execute(text("SELECT version_num FROM alembic_version"))
        print("alembic:", v.scalar())

asyncio.run(main())
