import asyncio

import pytest

from app.services import onec_db_schema


@pytest.mark.asyncio
async def test_ensure_onec_agent_tables_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    onec_db_schema._tables_ready = False
    calls = {"n": 0}

    async def fake_ensure() -> None:
        calls["n"] += 1
        onec_db_schema._tables_ready = True

    monkeypatch.setattr(onec_db_schema, "_tables_ready", False)

    async def wrapped() -> None:
        if onec_db_schema._tables_ready:
            return
        async with onec_db_schema._tables_lock:
            if onec_db_schema._tables_ready:
                return
            await fake_ensure()

    monkeypatch.setattr(onec_db_schema, "ensure_onec_agent_tables", wrapped)

    await asyncio.gather(
        onec_db_schema.ensure_onec_agent_tables(),
        onec_db_schema.ensure_onec_agent_tables(),
        onec_db_schema.ensure_onec_agent_tables(),
    )
    assert calls["n"] == 1
