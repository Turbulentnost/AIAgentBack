from __future__ import annotations

import asyncio

from app.db.session import AsyncSessionLocal
from app.services.document_card_service import DocumentCardService


async def main() -> None:
    async with AsyncSessionLocal() as session:
        result = await DocumentCardService(session).bootstrap_for_all_documents()
        await session.commit()
        print(
            f"Bootstrap завершён: создано {result.created}, "
            f"пропущено {result.skipped}, всего документов {result.total_documents}"
        )


if __name__ == "__main__":
    asyncio.run(main())
