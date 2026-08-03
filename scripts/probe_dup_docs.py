"""List all 1C documents with given Number (duplicate numbers)."""
from __future__ import annotations

import json
import sys
from urllib.parse import quote

import httpx

ROOT = __import__("pathlib").Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_pochta.config import get_settings  # noqa: E402

DOC_ENTITY = "Document_ТД_ВходящаяКорреспонденция"
NUMBERS = ["АЛ00-000760", "АЛ00-000762"]


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    settings = get_settings()
    base = settings.odata_base_url.rstrip("/") + "/"
    auth = (settings.odata_username, settings.odata_password)
    report = {}
    for number in NUMBERS:
        flt = f"Number eq '{number}'"
        url = (
            f"{base}{quote(DOC_ENTITY)}?$format=json"
            f"&$filter={quote(flt)}&$orderby=Date desc&$top=20"
        )
        items = httpx.get(url, auth=auth, timeout=120).json().get("value", [])
        report[number] = [
            {
                "Ref_Key": i.get("Ref_Key"),
                "Date": i.get("Date"),
                "Posted": i.get("Posted"),
                "DeletionMark": i.get("DeletionMark"),
                "Статус": i.get("Статус"),
            }
            for i in items
        ]
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
