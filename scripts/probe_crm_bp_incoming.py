"""Find CRM BP with incoming correspondence as subject."""
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
CRM_BP = "BusinessProcess_CRM_БизнесПроцесс"


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    settings = get_settings()
    base = settings.odata_base_url.rstrip("/") + "/"
    auth = (settings.odata_username, settings.odata_password)

    bp_url = f"{base}{quote(CRM_BP)}?$format=json&$orderby=Date desc&$top=2000"
    items = httpx.get(bp_url, auth=auth, timeout=120).raise_for_status().json().get("value", [])

    incoming = [
        {
            "Ref_Key": item.get("Ref_Key"),
            "Number": item.get("Number"),
            "Предмет": item.get("Предмет"),
            "Предмет_Type": item.get("Предмет_Type"),
            "Completed": item.get("Completed"),
            "DeletionMark": item.get("DeletionMark"),
            "Started": item.get("Started"),
        }
        for item in items
        if (item.get("Предмет_Type") or "").endswith(DOC_ENTITY)
    ]

    print(json.dumps({"incoming_subject_count": len(incoming), "samples": incoming[:10]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
