import asyncio

import pytest
from sqlalchemy import text

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


@pytest.mark.asyncio
async def test_ensure_onec_agent_tables_does_not_run_runtime_alter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    onec_db_schema._tables_ready = False
    executed: list[str] = []

    class FakeConn:
        async def run_sync(self, fn) -> None:
            class SyncConn:
                def execute(self, stmt) -> None:
                    executed.append(str(stmt))

            fn(SyncConn())

        async def execute(self, stmt, params=None) -> None:
            executed.append(str(stmt))

    class FakeEngine:
        def begin(self):
            return self

        async def __aenter__(self):
            return FakeConn()

        async def __aexit__(self, *args) -> None:
            return None

    monkeypatch.setattr(onec_db_schema, "_tables_ready", False)
    monkeypatch.setattr(
        "app.db.session.engine",
        FakeEngine(),
        raising=False,
    )

    def fake_create_all(sync_conn, *, tables, checkfirst) -> None:
        pass

    import app.db.base as db_base

    monkeypatch.setattr(db_base.Base.metadata, "create_all", fake_create_all)

    await onec_db_schema.ensure_onec_agent_tables()

    alter_stmts = [stmt for stmt in executed if "ALTER TABLE" in stmt.upper()]
    assert alter_stmts == []
    assert any("pg_advisory_xact_lock" in stmt for stmt in executed)
    assert onec_db_schema._tables_ready is True
