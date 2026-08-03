"""Compare working attachment vs strategy-A IB; probe Catalog_ВерсииФайлов."""
from __future__ import annotations

import json
import sys
from urllib.parse import quote

import httpx

sys.path.insert(0, "/app/src" if __import__("pathlib").Path("/app/src").exists() else str(__import__("pathlib").Path(__file__).resolve().parents[1] / "src"))

from agent_pochta.config import get_settings
from agent_pochta.services.odata_client import ODataClient

ENTITY = "Catalog_ТД_ВходящаяКорреспонденцияПрисоединенныеФайлы"
VER = "Catalog_ВерсииФайлов"
SKIP = {
    "ФайлХранилище_Base64Data",
    "ТекстХранилище",
    "ТекстХранилище_Base64Data",
    "odata.metadata",
}


def slim(rec: dict, client: ODataClient, ref: str) -> dict:
    out = {
        k: v
        for k, v in rec.items()
        if k not in SKIP and not (isinstance(v, str) and len(v) > 300)
    }
    out["b64_len"] = len(rec.get("ФайлХранилище_Base64Data") or "")
    out["stream_len"] = len(client.get_entity_stream(ENTITY, ref, "ФайлХранилище") or b"")
    return out


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    s = get_settings()
    base = s.odata_base_url.rstrip("/") + "/"
    auth = (s.odata_username, s.odata_password)
    client = ODataClient(
        s.odata_base_url,
        username=s.odata_username,
        password=s.odata_password,
        timeout_sec=120,
    )
    refs = {
        "ok760": "27997dc5-8689-11f1-984a-6cb31113810e",
        "A_ib": "ad4a073c-8a7e-11f1-9850-6cb31113810e",
    }
    report: dict = {"attachments": {}, "versions": {}}
    for label, ref in refs.items():
        url = f"{base}{quote(ENTITY)}(guid'{ref}')?$format=json"
        rec = httpx.get(url, auth=auth, timeout=120).json()
        report["attachments"][label] = slim(rec, client, ref)

    # Sample version entity shape
    r = httpx.get(f"{base}{quote(VER)}?$format=json&$top=1", auth=auth, timeout=60)
    report["versions"]["sample_status"] = r.status_code
    if r.status_code == 200:
        v = (r.json().get("value") or [{}])[0]
        report["versions"]["sample_keys"] = sorted(v.keys())
        report["versions"]["sample_slim"] = {
            k: v.get(k)
            for k in v
            if k not in SKIP and not (isinstance(v.get(k), str) and len(str(v.get(k))) > 200)
        }
        report["versions"]["sample_b64_len"] = len(v.get("ФайлХранилище_Base64Data") or "")
        # Try common owner fields for ok760
        owner_candidates = [
            "Владелец_Key",
            "Owner",
            "Owner_Key",
            "Файл_Key",
            "РодительскаяВерсия_Key",
            "Родитель_Key",
        ]
        report["versions"]["owner_like_keys"] = [k for k in owner_candidates if k in v]
        for fk in [k for k in v if k.endswith("_Key") or "Файл" in k or "Владел" in k]:
            report["versions"].setdefault("key_fields_sample", {})[fk] = v.get(fk)

    # Filter versions by Description match or by scanning recent
    for label, owner_ref in refs.items():
        found = []
        for field in ("Владелец_Key", "Файл_Key", "Owner_Key"):
            filt = f"{field} eq guid'{owner_ref}'"
            url = f"{base}{quote(VER)}?$format=json&$top=5&$filter={quote(filt)}"
            rr = httpx.get(url, auth=auth, timeout=60)
            found.append({"field": field, "status": rr.status_code, "body": rr.text[:300]})
            if rr.status_code == 200 and (rr.json().get("value") or []):
                items = rr.json()["value"]
                found[-1]["count"] = len(items)
                found[-1]["first"] = {
                    k: items[0].get(k)
                    for k in items[0]
                    if k not in SKIP and not (isinstance(items[0].get(k), str) and len(str(items[0].get(k))) > 120)
                }
                found[-1]["first_b64"] = len(items[0].get("ФайлХранилище_Base64Data") or "")
                break
        report["versions"][f"by_{label}"] = found

    out = "/app/data/temp/ok_vs_a_versions.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(json.dumps(report, ensure_ascii=False, indent=2)[:8000])
    print("saved", out)


if __name__ == "__main__":
    main()
