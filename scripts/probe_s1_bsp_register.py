"""Check if S1 IB attach has BSP binary register rows."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import httpx
from agent_pochta.config import get_settings
from agent_pochta.services.odata_client import ODataClient

REF = "85f40a55-8a81-11f1-9850-6cb31113810e"
ENTITY = "Catalog_ТД_ВходящаяКорреспонденцияПрисоединенныеФайлы"
DOC = "2b55a128-8a44-11f1-9850-6cb31113810e"


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
    meta = c.get_by_key(ENTITY, REF) or {}
    keys = [
        "Ref_Key",
        "Description",
        "ТипХраненияФайла",
        "Том_Key",
        "ПутьКФайлу",
        "Размер",
        "DeletionMark",
        "Изменил_Key",
        "ФайлХранилище_Type",
    ]
    out = {k: meta.get(k) for k in keys}
    b64 = meta.get("ФайлХранилище_Base64Data") or ""
    out["b64_len"] = len(b64) if isinstance(b64, str) else 0
    out["stream_len"] = len(c.get_entity_stream(ENTITY, REF, "ФайлХранилище") or b"")
    print("=== S1 meta")
    print(json.dumps(out, ensure_ascii=False, indent=2))

    auth = (s.odata_username, s.odata_password)
    base = s.odata_base_url.rstrip("/") + "/"
    xml = httpx.get(base + "$metadata", auth=auth, timeout=120).text
    names = re.findall(r'EntityType Name="([^"]+)"', xml)
    interesting = [
        n
        for n in names
        if any(
            k in n
            for k in (
                "ХранилищеФайлов",
                "ДвоичныеДанные",
                "Двоичн",
                "СведенияОФайлах",
                "ВерсииФайлов",
            )
        )
    ]
    print("=== binary-ish entities")
    for n in interesting:
        print(n)

    for ent in interesting:
        for filt in (
            f"Файл eq cast(guid'{REF}','Edm.String')",
            f"Файл_Key eq guid'{REF}'",
            f"Файл eq guid'{REF}'",
            f"ПрисоединенныйФайл_Key eq guid'{REF}'",
            f"Объект eq guid'{REF}'",
            f"Объект_Key eq guid'{REF}'",
        ):
            try:
                rows = c.fetch_filtered(ent, filter_expr=filt, page_size=5)
            except Exception:
                continue
            if rows:
                print(f"HIT {ent} filter={filt} count={len(rows)}")
                sample = rows[0]
                slim = {
                    k: (
                        f"<len={len(v)}>"
                        if isinstance(v, str) and len(v) > 120
                        else v
                    )
                    for k, v in sample.items()
                }
                print(json.dumps(slim, ensure_ascii=False)[:1500])
                break

    # all attachments on doc now
    rows = c.fetch_filtered(
        ENTITY, filter_expr=f"ВладелецФайла_Key eq guid'{DOC}'", page_size=50
    )
    print("=== doc files")
    for r in sorted(rows, key=lambda x: str(x.get("ДатаСоздания") or ""), reverse=True):
        b64 = r.get("ФайлХранилище_Base64Data") or ""
        print(
            json.dumps(
                {
                    "Ref_Key": r.get("Ref_Key"),
                    "Description": r.get("Description"),
                    "kind": r.get("ТипХраненияФайла"),
                    "tom": r.get("Том_Key"),
                    "path": r.get("ПутьКФайлу"),
                    "size": r.get("Размер"),
                    "del": r.get("DeletionMark"),
                    "b64_len": len(b64) if isinstance(b64, str) else 0,
                    "Изменил": r.get("Изменил_Key"),
                },
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    main()
