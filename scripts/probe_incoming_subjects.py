"""Scan tasks/BP for incoming correspondence subjects."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.parse import quote

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_pochta.config import get_settings  # noqa: E402

DOC_ENTITY = "Document_ТД_ВходящаяКорреспонденция"
ENTITIES = ("BusinessProcess_Задание", "Task_ЗадачаИсполнителя", "BusinessProcess_CRM_БизнесПроцесс")


def scan_entity(base: str, auth: tuple[str, str], entity: str, *, top: int) -> dict:
    url = f"{base}{quote(entity)}?$format=json&$orderby=Date desc&$top={top}"
    items = httpx.get(url, auth=auth, timeout=120).raise_for_status().json().get("value", [])
    incoming = [
        {
            "Ref_Key": item.get("Ref_Key"),
            "Number": item.get("Number"),
            "Предмет": item.get("Предмет"),
            "Предмет_Type": item.get("Предмет_Type"),
            "Completed": item.get("Completed"),
            "DeletionMark": item.get("DeletionMark"),
        }
        for item in items
        if (item.get("Предмет_Type") or "").endswith(DOC_ENTITY)
    ]
    return {"scanned": len(items), "incoming_subject_count": len(incoming), "samples": incoming[:5]}


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    settings = get_settings()
    base = settings.odata_base_url.rstrip("/") + "/"
    auth = (settings.odata_username, settings.odata_password)
    report = {entity: scan_entity(base, auth, entity, top=5000) for entity in ENTITIES}
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
