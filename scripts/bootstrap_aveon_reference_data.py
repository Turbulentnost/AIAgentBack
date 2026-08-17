"""Bootstrap missing Aveon reference data after deploy."""
from __future__ import annotations

import asyncio
import io
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from openpyxl import Workbook
from sqlalchemy import func, select

from app.agents.document_analysis_agent.temp_schedule_merge import (
    SUPPLIER_COUNTRY_RUSSIA,
    _META_HEADERS,
)
from app.db.session import AsyncSessionLocal
from app.models.aveon_shipment_schedule import AveonShipmentScheduleVersion
from app.models.onec_production_plan import OnecProductionPlanHeader
from app.services.aveon_shipment_schedule_service import AveonShipmentScheduleService
from app.services.onec_daily_sync import sync_onec_production_plan_step


def _find_russia_xlsx() -> Path | None:
    secrets_dir = ROOT / "secrets"
    data_dir = ROOT / "data" / "aveon"
    search_roots = (
        secrets_dir,
        data_dir,
        ROOT.parent,
        Path(r"C:\Users\uaa\Desktop\мусор\AI Platform\AIAgentBack\secrets"),
    )
    patterns = (
        "*график*отгруз*.xlsx",
        "*GRAFIK*.xlsx",
        "*отгруз*.xlsx",
        "russia*.xlsx",
        "merged_schedule.xlsx",
    )
    candidates: list[Path] = []
    for root in search_roots:
        if not root.exists():
            continue
        for pattern in patterns:
            candidates.extend(root.glob(pattern))
    # Prefer real schedules over bootstrap placeholder names.
    for path in sorted(candidates, key=lambda item: item.stat().st_mtime, reverse=True):
        name = path.name.casefold()
        if "bootstrap" in name or name.startswith("~$"):
            continue
        return path
    return None


def _build_bootstrap_russia_workbook() -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "График"
    ws.append([*_META_HEADERS, date(2026, 8, 12), date(2026, 8, 20)])
    for index in range(1, 51):
        ws.append(
            [
                f"Деталь {index}",
                "Спецификация",
                SUPPLIER_COUNTRY_RUSSIA,
                index,
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                index,
                "",
            ]
        )
    buf = io.BytesIO()
    wb.save(buf)
    wb.close()
    return buf.getvalue()


async def ensure_production_plan() -> None:
    async with AsyncSessionLocal() as db:
        header_count = await db.scalar(select(func.count()).select_from(OnecProductionPlanHeader))
        if header_count:
            print("Production plan already in DB")
            return
        result = await sync_onec_production_plan_step(db)
        await db.commit()
        print("Production plan sync:", result)


async def ensure_russia_shipment() -> None:
    async with AsyncSessionLocal() as db:
        active = await AveonShipmentScheduleService(db).get_active_russia()
        if active is not None:
            print(f"Russia shipment already active: {active.version.file_name}")
            return

        path = _find_russia_xlsx()
        if path is not None:
            raw = path.read_bytes()
            filename = path.name
            print(f"Russia shipment: uploading {path}")
        else:
            raw = _build_bootstrap_russia_workbook()
            filename = "russia_shipment_schedule.xlsx"
            print(
                "Russia shipment: no xlsx in secrets/ — uploaded bootstrap placeholder. "
                "Replace with secrets/russia_shipment_schedule.xlsx from dev and re-run."
            )

        version = await AveonShipmentScheduleService(db).save_russia_upload(
            filename=filename,
            raw=raw,
            created_by_user_id=None,
            reason="deploy_bootstrap",
        )
        await db.commit()
        preview_rows = len(version.preview_json or [])
        print(f"Russia shipment uploaded: {version.file_name} ({preview_rows} preview rows)")


async def main() -> None:
    await ensure_production_plan()
    await ensure_russia_shipment()


if __name__ == "__main__":
    asyncio.run(main())
