"""E2E: login → specs → calculate → excel на свежей SQLite без Postgres."""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

tmp = Path(tempfile.mkdtemp(prefix="aveon_desktop_e2e_"))
sqlite_path = tmp / "aveon_desktop.db"

os.environ["DESKTOP_MODE"] = "1"
os.environ["DESKTOP_SQLITE_PATH"] = str(sqlite_path)
os.environ["AUTH_ALLOW_JWT_WITHOUT_SESSION"] = "true"
os.environ["ONEC_DAILY_SYNC_ENABLED"] = "false"
os.environ["ONEC_INPROCESS_SYNC_ENABLED"] = "false"
os.environ["DOCUMENT_ANALYSIS_REQUIRE_AUTH"] = "true"

from app_desktop.bootstrap_env import load_desktop_env

load_desktop_env()
os.environ["DESKTOP_SQLITE_PATH"] = str(sqlite_path)
os.environ["AUTH_ALLOW_JWT_WITHOUT_SESSION"] = "true"

from app_desktop.bootstrap_auth import bootstrap_desktop_auth_store, bootstrap_desktop_catalog
from app_desktop.main import create_desktop_app
import uvicorn


async def prepare() -> None:
    await bootstrap_desktop_auth_store()
    catalog = await bootstrap_desktop_catalog()
    print("catalog", catalog)
    assert catalog.get("ok"), catalog
    assert int(catalog.get("saved_specs") or catalog.get("db_specs") or 0) > 0, catalog


async def run_http() -> None:
    app = create_desktop_app()
    config = uvicorn.Config(app, host="127.0.0.1", port=18768, log_level="warning")
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    try:
        for _ in range(60):
            if server.started:
                break
            await asyncio.sleep(0.2)
        async with httpx.AsyncClient(base_url="http://127.0.0.1:18768/api/v1", timeout=60.0) as client:
            login = await client.post(
                "/auth/login",
                json={"email": "bugata.pavel@local.dev", "password": "Bugata2026!"},
            )
            login.raise_for_status()
            token = login.json()["access_token"]
            headers = {"Authorization": f"Bearer {token}"}

            me = await client.get("/auth/me", headers=headers)
            me.raise_for_status()
            assert me.json()["email"] == "bugata.pavel@local.dev"
            print("auth ok")

            specs = await client.get(
                "/agents/document-analysis/resource-specs?limit=1000",
                headers=headers,
            )
            specs.raise_for_status()
            items = [i for i in specs.json()["items"] if i.get("materials_count", 0) > 0]
            print("specs", len(items))
            assert len(items) > 0, specs.json()

            pick = items[0]
            calc = await client.post(
                "/agents/document-analysis/material-calculator",
                headers=headers,
                json={"items": [{"spec_ref_key": pick["ref_key"], "quantity": 2}]},
            )
            calc.raise_for_status()
            calc_body = calc.json()
            lines = calc_body.get("lines") or []
            print("calc lines", len(lines), "warnings", calc_body.get("warnings"))
            assert len(lines) > 0, calc_body

            export = await client.post(
                "/agents/document-analysis/material-calculator/export",
                headers=headers,
                json={
                    "lines": [
                        {
                            "nomenclature_key": line["nomenclature_key"],
                            "code": line.get("code") or "",
                            "name": line.get("name") or "",
                            "unit": line.get("unit") or "",
                            "total_qty": line["total_qty"],
                        }
                        for line in lines
                    ]
                },
            )
            export.raise_for_status()
            assert "spreadsheet" in export.headers.get("content-type", "") or export.content[:2] == b"PK"
            print("export bytes", len(export.content))
            print("E2E OK", sqlite_path)
    finally:
        server.should_exit = True
        await task


async def main() -> int:
    await prepare()
    await run_http()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
