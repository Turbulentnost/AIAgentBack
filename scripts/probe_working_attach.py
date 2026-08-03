"""Find a working manual volume attachment for comparison."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_pochta.config import get_settings
from agent_pochta.services.odata_client import ODataClient

ENTITY = "Catalog_ТД_ВходящаяКорреспонденцияПрисоединенныеФайлы"
CANDIDATES = [
    "27997dc5-8689-11f1-984a-6cb31113810e",  # prior working 760 claim
    "598a6fa7-8765-11f1-984c-6cb31113810e",  # manual outlook on 762?
]
KEYS = (
    "Ref_Key",
    "Description",
    "ТипХраненияФайла",
    "Том_Key",
    "ПутьКФайлу",
    "Размер",
    "ДатаСоздания",
    "Изменил_Key",
    "Автор_Key",
    "ФайлХранилище_Type",
)


def slim(m: dict) -> dict:
    out = {k: m.get(k) for k in KEYS}
    b64 = m.get("ФайлХранилище_Base64Data") or ""
    out["b64_len"] = len(b64) if isinstance(b64, str) else 0
    return out


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    s = get_settings()
    c = ODataClient(
        s.odata_base_url,
        username=s.odata_username,
        password=s.odata_password,
        timeout_sec=120,
    )
    for ref in CANDIDATES:
        m = c.get_by_key(ENTITY, ref)
        print("===", ref, "found" if m else "MISSING")
        if m:
            print(json.dumps(slim(m), ensure_ascii=False, indent=2))

    # Doc attachments for 003921 only (owner filter works)
    doc = "2b55a128-8a44-11f1-9850-6cb31113810e"
    rows = c.fetch_filtered(
        ENTITY, filter_expr=f"ВладелецФайла_Key eq guid'{doc}'", page_size=50
    )
    print("=== doc rows", len(rows))
    for r in sorted(rows, key=lambda x: str(x.get("ДатаСоздания") or ""), reverse=True):
        print(json.dumps(slim(r), ensure_ascii=False))

    # Try find any volume with path via Description patterns known working
    for desc in ("АЛ00-000760", "АЛ00-000762", "НП00-003900", "НП00-003877"):
        try:
            rows = c.fetch_filtered(
                ENTITY,
                filter_expr=f"Description eq '{desc}'",
                page_size=10,
            )
        except Exception as exc:
            print("filter err", desc, str(exc)[:120])
            continue
        print(f"=== desc {desc} count={len(rows)}")
        for r in rows:
            print(json.dumps(slim(r), ensure_ascii=False))


if __name__ == "__main__":
    main()
