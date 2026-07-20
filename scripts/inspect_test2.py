import asyncio
import json
from sqlalchemy import text
from app.db.session import AsyncSessionLocal

DEPT_ID = "b6ea8bf1-7cc5-4dec-a81f-42a4cc682d6c"

async def main():
    async with AsyncSessionLocal() as db:
        r = await db.execute(text(
            "SELECT id, status, processed_documents, failed_documents, total_documents, "
            "current_step, error_message, started_at, finished_at "
            "FROM nd_department_analysis_runs WHERE department_id = :did "
            "ORDER BY created_at DESC LIMIT 5"
        ), {"did": DEPT_ID})
        print("RUNS:")
        for row in r.fetchall():
            print(row)
        r2 = await db.execute(text(
            "SELECT dc.document_code, dc.title, dc.extraction_status, dc.raw_extracted_json "
            "FROM nd_structural_document_cards dc "
            "WHERE dc.knowledge_base_id = '845e2a10-f57b-4607-932c-86d38c397d36' "
            "ORDER BY dc.updated_at"
        ))
        print("CARDS:")
        for row in r2.fetchall():
            raw = row[3] or {}
            print(row[0], row[1], row[2], raw.get("error", "")[:200] if raw.get("error") else "")

asyncio.run(main())
