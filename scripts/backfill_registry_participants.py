"""Backfill meeting_registry_entries.participants from memo Redis cache."""
from __future__ import annotations

import asyncio

from sqlalchemy import select

from app.db.session import AsyncSessionLocal
from app.models.meeting_registry import MeetingRegistryEntry
from app.services.meeting_attendees import participants_from_detail
from app.services.meeting_memo_cache import MeetingMemoCacheService, MemoCacheMissError


async def main() -> None:
    updated = 0
    skipped = 0
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(MeetingRegistryEntry))
        entries = list(result.scalars().all())
        cache = MeetingMemoCacheService()
        for entry in entries:
            current = entry.participants if isinstance(entry.participants, list) else []
            if any(isinstance(name, str) and name.strip() for name in current):
                skipped += 1
                continue
            try:
                detail, _, _ = await cache.get_memo_detail(entry.memo_ref_key)
            except MemoCacheMissError:
                skipped += 1
                continue
            names = participants_from_detail(detail)
            if not names:
                skipped += 1
                continue
            entry.participants = names
            entry.participants_count = len(names)
            updated += 1
        await db.commit()
    print(f"updated={updated} skipped={skipped}")


if __name__ == "__main__":
    asyncio.run(main())
