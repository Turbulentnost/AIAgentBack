"""Search OData metadata for process entities related to incoming correspondence."""
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

    needles = ("Входящ", "Корресп", "Документооб", "Process", "Процесс", "Задание")
    entity_sets = re.findall(r'EntitySet Name="([^"]+)"', meta)
    hits = sorted({e for e in entity_sets if any(n in e for n in needles)})

    # Property hits inside incoming doc block
    doc_marker = 'EntityType Name="Document_ТД_ВходящаяКорреспонденция"'
    idx = meta.find(doc_marker)
    doc_block = ""
    if idx >= 0:
        end = meta.find("</EntityType>", idx)
        doc_block = meta[idx:end] if end >= 0 else ""

    # Search whole metadata for properties referencing incoming doc
    ref_hits = []
    for m in re.finditer(r'Property Name="([^"]+)"[^>]*Type="([^"]+)"', doc_block):
        ref_hits.append({"name": m.group(1), "type": m.group(2)})

    # Entities whose metadata mention incoming doc type
    incoming_type = "Document_ТД_ВходящаяКорреспонденция"
    entity_hits = []
    for es in entity_sets:
        marker = f'EntityType Name="{es}"'
        pos = meta.find(marker)
        if pos < 0:
            continue
        end = meta.find("</EntityType>", pos)
        block = meta[pos : end if end >= 0 else pos + 5000]
        if incoming_type in block or "ВходящаяКорреспонденция" in block:
            props = re.findall(r'Property Name="([^"]+)"', block)
            entity_hits.append({"entity": es, "props": props[:20], "prop_count": len(props)})

    print(
        json.dumps(
            {
                "entity_set_hits_count": len(hits),
                "entity_set_hits": hits[:80],
                "incoming_doc_properties": ref_hits,
                "entities_mentioning_incoming_doc": entity_hits[:30],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
