"""Повтор операций БД при deadlock PostgreSQL."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

from sqlalchemy.exc import DBAPIError

T = TypeVar("T")

_DEADLOCK_TYPES = frozenset({"DeadlockDetectedError"})


def _is_deadlock_error(exc: BaseException) -> bool:
    if isinstance(exc, DBAPIError):
        orig = getattr(exc, "orig", None)
        if orig is not None and type(orig).__name__ in _DEADLOCK_TYPES:
            return True
    cause = getattr(exc, "__cause__", None)
    if cause is not None and type(cause).__name__ in _DEADLOCK_TYPES:
        return True
    return False


async def run_with_deadlock_retry(
    operation: Callable[[], Awaitable[T]],
    *,
    attempts: int = 3,
    base_delay_sec: float = 0.05,
) -> T:
    """Выполняет async-операцию с коротким backoff при DeadlockDetectedError."""
    last_error: BaseException | None = None
    for attempt in range(max(1, attempts)):
        try:
            return await operation()
        except DBAPIError as exc:
            if not _is_deadlock_error(exc) or attempt >= attempts - 1:
                raise
            last_error = exc
        except Exception as exc:
            if not _is_deadlock_error(exc) or attempt >= attempts - 1:
                raise
            last_error = exc
        await asyncio.sleep(base_delay_sec * (2**attempt))
    assert last_error is not None
    raise last_error
