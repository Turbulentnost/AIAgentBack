"""Search metadata registers/catalogs linking docs to business processes."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

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
    meta = httpx.get(f"{base}$metadata", auth=auth, timeout=120).raise_for_status().text

    entity_sets = re.findall(r'EntitySet Name="([^"]+)"', meta)
    hits = []
    for es in entity_sets:
        if not any(x in es for x in ("Register", "Catalog_ТД_", "BusinessProcess")):
            continue
        marker = f'EntityType Name="{es}"'
        if es.endswith("_RecordType"):
            marker = f'EntityType Name="{es[:-11]}"'
        pos = meta.find(marker)
        if pos < 0:
            continue
        end = meta.find("</EntityType>", pos)
        block = meta[pos : end if end >= 0 else pos + 4000]
        if "ВходящаяКорреспонденция" not in block and "BusinessProcess" not in block and "БизнесПроцесс" not in block:
            continue
        props = re.findall(r'Property Name="([^"]+)"', block)
        hits.append({"entity": es, "props": props})

    print(json.dumps({"hits": hits[:40], "count": len(hits)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
