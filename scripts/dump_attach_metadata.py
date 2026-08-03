"""Dump OData metadata ref properties for attached files entity."""
from __future__ import annotations

import re
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_pochta.config import get_settings  # noqa: E402

ENTITY = "Catalog_ТД_ВходящаяКорреспонденцияПрисоединенныеФайлы"


def main() -> None:
    settings = get_settings()
    base = settings.odata_base_url.rstrip("/") + "/"
    auth = (settings.odata_username, settings.odata_password)
    meta = httpx.get(f"{base}$metadata", auth=auth, timeout=60).text
    idx = meta.find(f'EntityType Name="{ENTITY}"')
    block = meta[idx : idx + 15000]
    props = re.findall(r'Property Name="([^"]+)"[^>]*Type="([^"]+)"', block)
    for name, typ in props:
        if any(x in name for x in ("Key", "Редакт", "Измен", "Автор", "Том", "Папка", "Parent", "Владелец")):
            print(f"{name}: {typ}")


if __name__ == "__main__":
    main()
