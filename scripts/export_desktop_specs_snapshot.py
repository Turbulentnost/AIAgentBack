"""Выгрузка resource specs из Postgres в snapshot для desktop SQLite."""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")
os.environ.pop("DESKTOP_SQLITE_PATH", None)
os.environ.pop("DESKTOP_MODE", None)


async def main() -> int:
    from sqlalchemy import select

    from app.db.session import AsyncSessionLocal
    from app.models.onec_nomenclature import OnecNomenclature
    from app.models.onec_resource_spec import (
        OnecResourceSpec,
        OnecResourceSpecMaterial,
        OnecResourceSpecOutput,
    )

    out = ROOT / "app_desktop" / "data" / "resource_specs_snapshot.json"
    out.parent.mkdir(parents=True, exist_ok=True)

    async with AsyncSessionLocal() as db:
        specs = (await db.execute(select(OnecResourceSpec))).scalars().all()
        mats = (await db.execute(select(OnecResourceSpecMaterial))).scalars().all()
        outs = (await db.execute(select(OnecResourceSpecOutput))).scalars().all()
        noms = (await db.execute(select(OnecNomenclature))).scalars().all()

    mats_by: dict[str, list] = {}
    for m in mats:
        mats_by.setdefault(m.spec_ref_key, []).append(
            {
                "line_number": m.line_number,
                "nomenclature_key": m.nomenclature_key,
                "nomenclature_code": m.nomenclature_code,
                "nomenclature_name": m.nomenclature_name,
                "characteristic_key": m.characteristic_key,
                "qty": float(m.qty or 0),
                "packaging_key": m.packaging_key,
                "unit": m.unit or "",
                "produced_in_process": bool(m.produced_in_process),
                "alternative": bool(m.alternative),
            }
        )
    outs_by: dict[str, list] = {}
    for o in outs:
        outs_by.setdefault(o.spec_ref_key, []).append(
            {
                "line_number": o.line_number,
                "nomenclature_key": o.nomenclature_key,
                "nomenclature_code": o.nomenclature_code,
                "nomenclature_name": o.nomenclature_name,
                "characteristic_key": o.characteristic_key,
                "qty": float(o.qty or 0),
                "packaging_key": o.packaging_key,
                "description": o.description or "",
            }
        )

    payload = {
        "version": 1,
        "specs": [
            {
                "ref_key": s.ref_key,
                "code": s.code or "",
                "description": s.description or "",
                "status": s.status or "",
                "process_type": s.process_type or "",
                "is_folder": bool(s.is_folder),
                "deletion_mark": bool(s.deletion_mark),
                "main_product_key": s.main_product_key or "",
                "main_product_code": s.main_product_code or "",
                "main_product_name": s.main_product_name or "",
                "main_product_qty": float(s.main_product_qty or 0),
                "valid_from": s.valid_from.isoformat() if s.valid_from else None,
                "valid_to": s.valid_to.isoformat() if s.valid_to else None,
                "materials_count": int(s.materials_count or len(mats_by.get(s.ref_key, []))),
                "outputs_count": int(s.outputs_count or len(outs_by.get(s.ref_key, []))),
                "materials": mats_by.get(s.ref_key, []),
                "outputs": outs_by.get(s.ref_key, []),
            }
            for s in specs
        ],
        "nomenclature": [
            {
                "ref_key": n.ref_key,
                "code": n.code or "",
                "name": n.name or "",
                "country_key": n.country_key or "",
                "country_of_origin": n.country_of_origin or "",
                "unit_key": n.unit_key or "",
                "unit": n.unit or "",
            }
            for n in noms
        ],
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(
        f"wrote {out} specs={len(payload['specs'])} "
        f"materials={sum(len(s['materials']) for s in payload['specs'])} "
        f"bytes={out.stat().st_size}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
