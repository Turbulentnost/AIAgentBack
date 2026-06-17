"""Re-run extraction for failed Test 2 document cards."""
from __future__ import annotations

import asyncio
import uuid

from sqlalchemy import select, text

from app.db.session import AsyncSessionLocal
from app.models.enums import NdExtractionStatus
from app.models.nd_control_structural import DocumentCard
from app.services.nd_document_card_extraction_service import NdDocumentCardExtractionService

DEPT_ID = uuid.UUID("b6ea8bf1-7cc5-4dec-a81f-42a4cc682d6c")
KB_ID = uuid.UUID("845e2a10-f57b-4607-932c-86d38c397d36")


async def main() -> None:
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(DocumentCard).where(
                DocumentCard.knowledge_base_id == KB_ID,
                DocumentCard.extraction_status == NdExtractionStatus.FAILED,
            )
        )
        cards = list(result.scalars().all())
        print(f"Failed cards: {len(cards)}")
        service = NdDocumentCardExtractionService(db)
        for card in cards:
            print(f"Re-extracting {card.document_code} {card.title} ({card.document_id})")
            try:
                updated = await service.extract_document_card(str(card.document_id))
                await db.commit()
                print(f"  -> {updated.extraction_status.value}")
            except Exception as exc:
                await db.rollback()
                print(f"  -> ERROR: {exc}")

        summary = await db.execute(
            text(
                "SELECT extraction_status, count(*) FROM nd_structural_document_cards "
                "WHERE knowledge_base_id = :kb GROUP BY extraction_status"
            ),
            {"kb": KB_ID},
        )
        print("STATUS SUMMARY:", summary.fetchall())


if __name__ == "__main__":
    asyncio.run(main())
