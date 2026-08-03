"""Probe automatic document workflow catalog."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.parse import quote

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_pochta.config import get_settings  # noqa: E402

ENTITY = "Catalog_ТД_АвтоматическиеПроцессыДокументооборота"


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    settings = get_settings()
    base = settings.odata_base_url.rstrip("/") + "/"
    auth = (settings.odata_username, settings.odata_password)
    url = f"{base}{quote(ENTITY)}?$format=json&$top=20"
    items = httpx.get(url, auth=auth, timeout=120).raise_for_status().json().get("value", [])
    print(
        json.dumps(
            {
                "count": len(items),
                "sample_keys": sorted(items[0].keys()) if items else [],
                "samples": items[:5],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
