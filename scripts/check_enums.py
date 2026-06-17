import asyncio
from sqlalchemy import text
from app.db.session import AsyncSessionLocal

async def main():
    async with AsyncSessionLocal() as db:
        r = await db.execute(text(
            "SELECT enumlabel FROM pg_enum e JOIN pg_type t ON e.enumtypid=t.oid "
            "WHERE t.typname='departmentanalysisrunstatus'"
        ))
        print("run status:", [x[0] for x in r.fetchall()])

asyncio.run(main())
