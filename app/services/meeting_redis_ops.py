from __future__ import annotations

import asyncio

from app.services.meeting_redis import get_meeting_redis

_REDIS_RETRY_ATTEMPTS = 2
_REDIS_RETRY_DELAY_SECONDS = 0.05


async def meeting_redis_get(key: str) -> str | None:
    client = get_meeting_redis()
    last_error: Exception | None = None
    for attempt in range(_REDIS_RETRY_ATTEMPTS):
        try:
            return await client.get(key)
        except Exception as exc:
            last_error = exc
            if attempt + 1 < _REDIS_RETRY_ATTEMPTS:
                await asyncio.sleep(_REDIS_RETRY_DELAY_SECONDS)
    if last_error is not None:
        raise last_error
    return None


async def meeting_redis_setex(key: str, ttl_seconds: int, value: str) -> None:
    client = get_meeting_redis()
    last_error: Exception | None = None
    for attempt in range(_REDIS_RETRY_ATTEMPTS):
        try:
            await client.setex(key, ttl_seconds, value)
            return
        except Exception as exc:
            last_error = exc
            if attempt + 1 < _REDIS_RETRY_ATTEMPTS:
                await asyncio.sleep(_REDIS_RETRY_DELAY_SECONDS)
    if last_error is not None:
        raise last_error
