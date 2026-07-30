"""Query full OData attachment metadata for 762 vs working 760."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.parse import quote

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_pochta.config import get_settings  # noqa: E402
from agent_pochta.services.odata_attached_file import (  # noqa: E402
    load_attached_file_field_map,
    read_attached_file_storage_bytes,
)
from agent_pochta.services.odata_client import ODataClient  # noqa: E402

OWNER_762 = "18516943-871f-11f1-984b-6cb31113810e"
REF_OK = "27997dc5-8689-11f1-984a-6cb31113810e"
ENTITY = "Catalog_ТД_ВходящаяКорреспонденцияПрисоединенныеФайлы"
REF_KEYS = [
    "9da68ad3-8726-11f1-984b-6cb31113810e",
    "ba6972cd-8727-11f1-984b-6cb31113810e",
]


def fetch(base: str, auth, ref: str) -> dict:
    url = f"{base}{quote(ENTITY)}(guid'{ref}')?$format=json"
    resp = httpx.get(url, auth=auth, timeout=120)
    if resp.status_code == 404:
        return {"_missing": True, "ref": ref}
    resp.raise_for_status()
    return resp.json()


def list_owner(base: str, auth) -> list[dict]:
    flt = f"ВладелецФайла_Key eq guid'{OWNER_762}'"
    url = (
        f"{base}{quote(ENTITY)}?$format=json"
        f"&$filter={quote(flt)}&$orderby=ДатаСоздания desc&$top=20"
    )
    return httpx.get(url, auth=auth, timeout=120).json().get("value", [])


def ref_fields(record: dict) -> dict:
    out = {}
    for k, v in sorted(record.items()):
        if k.endswith("_Key") or k.endswith("_Type") or "Владелец" in k:
            out[k] = v
    return out


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    settings = get_settings()
    base = settings.odata_base_url.rstrip("/") + "/"
    auth = (settings.odata_username, settings.odata_password)
    fm = load_attached_file_field_map()
    client = ODataClient(
        settings.odata_base_url,
        username=settings.odata_username,
        password=settings.odata_password,
        timeout_sec=120,
    )

    ok = fetch(base, auth, REF_OK)
    items = list_owner(base, auth)
    report = {
        "owner_762_attachments": len(items),
        "refs_on_owner": [
            {
                "Ref_Key": i.get("Ref_Key"),
                "ДатаСоздания": i.get("ДатаСоздания"),
                "DeletionMark": i.get("DeletionMark"),
                "ref_fields": ref_fields(i),
            }
            for i in items
        ],
        "known_refs": {},
        "ok_760_ref_fields": ref_fields(ok),
    }

    for ref in REF_KEYS:
        rec = fetch(base, auth, ref)
        content = b""
        if not rec.get("_missing"):
            content = read_attached_file_storage_bytes(
                client, entity=ENTITY, ref_key=ref, field_map=fm
            )
        slim = {}
        if not rec.get("_missing"):
            for k in sorted(rec.keys()):
                if k.startswith("@") or "Base64Data" in k:
                    continue
                slim[k] = rec.get(k)
        report["known_refs"][ref] = {
            "exists": not rec.get("_missing"),
            "record": slim if not rec.get("_missing") else {"_missing": True},
            "ref_fields": ref_fields(rec) if not rec.get("_missing") else {},
            "bytes": len(content),
            "cfb": content[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",
        }

    if items:
        cur = items[0]
        cur_ref = cur.get("Ref_Key", "")
        diff = {}
        for k in sorted(set(ok.keys()) | set(cur.keys())):
            if k.startswith("@") or k.endswith("_Base64Data"):
                continue
            ov, cv = ok.get(k), cur.get(k)
            if ov != cv:
                diff[k] = {"760-ok": ov, "762-cur": cv}
        report["field_diff_vs_760"] = diff
        report["762_current_ref"] = cur_ref

    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
