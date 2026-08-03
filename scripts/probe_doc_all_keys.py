"""Dump all keys from a running incoming doc."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.parse import quote

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_pochta.config import get_settings  # noqa: E402


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    settings = get_settings()
    base = settings.odata_base_url.rstrip("/") + "/"
    auth = (settings.odata_username, settings.odata_password)
    entity = "Document_ТД_ВходящаяКорреспонденция"
    ref = "5185c652-9aeb-11f0-9710-6cb3111380bc"
    doc = httpx.get(
        f"{base}{quote(entity)}(guid'{ref}')?$format=json",
        auth=auth,
        timeout=120,
    ).raise_for_status().json()
    print(json.dumps({"keys": sorted(doc), "doc": doc}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
