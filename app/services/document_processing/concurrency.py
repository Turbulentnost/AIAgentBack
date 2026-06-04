from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from functools import partial
from typing import Any, TypeVar


T = TypeVar("T")


async def run_blocking_document_task(func: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """Run blocking parser/storage code without blocking the async event loop."""

    return await asyncio.to_thread(partial(func, *args, **kwargs))


async def run_async_document_task(func: Callable[..., Coroutine[Any, Any, T]]) -> T:
    """Run async document-processing calls without an artificial concurrency limit."""

    return await func()
