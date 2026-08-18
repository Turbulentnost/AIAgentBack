import pytest
from sqlalchemy.exc import DBAPIError

from app.db.deadlock_retry import run_with_deadlock_retry


class DeadlockDetectedError(Exception):
    pass


@pytest.mark.asyncio
async def test_run_with_deadlock_retry_succeeds_on_second_attempt() -> None:
    attempts = {"n": 0}

    async def operation() -> str:
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise DBAPIError("stmt", {}, DeadlockDetectedError("deadlock"))
        return "ok"

    result = await run_with_deadlock_retry(operation, attempts=3, base_delay_sec=0)
    assert result == "ok"
    assert attempts["n"] == 2


@pytest.mark.asyncio
async def test_run_with_deadlock_retry_reraises_non_deadlock() -> None:
    async def operation() -> None:
        raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        await run_with_deadlock_retry(operation, attempts=3, base_delay_sec=0)
