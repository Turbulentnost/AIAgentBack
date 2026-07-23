"""Compare OData attached file records: working vs broken docs."""
from __future__ import annotations

import base64
import json
import sys
from pathlib import Path
from urllib.parse import quote

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_pochta.config import get_settings  # noqa: E402
from agent_pochta.services.odata_attached_file import load_attached_file_field_map  # noqa: E402

DOCS = [
    ("НП00-003822", "c66512e2-85c4-11f1-9849-6cb31113810e", "working"),
    ("НП00-003870", "ccb7ab6d-8653-11f1-984a-6cb31113810e", "fixed-test"),
    ("НП00-003876", "e9e1b18c-8669-11f1-984a-6cb31113810e", "broken"),
]
EML_DESC = "Входящее_письмо"
KEY_FIELDS = [
    "Ref_Key",
    "Description",
    "Расширение",
    "ВладелецФайла_Key",
    "Размер",
    "ТипХраненияФайла",
    "ДатаСоздания",
    "ДатаМодификацииУниверсальная",
    "ФайлХранилище_Type",
    "Том_Key",
    "Автор_Key",
]


def fetch_files(base: str, auth, entity: str, owner: str) -> list[dict]:
    flt = f"ВладелецФайла_Key eq guid'{owner}'"
    url = f"{base}{quote(entity)}?$format=json&$filter={quote(flt)}&$orderby=Ref_Key desc"
    with httpx.Client(timeout=60, auth=auth) as client:
        r = client.get(url)
        r.raise_for_status()
        return r.json().get("value", [])


def stream_bytes(base: str, auth, entity: str, ref_key: str) -> tuple[int, str]:
    url = f"{base}{quote(entity)}(guid'{ref_key}')/ФайлХранилище"
    with httpx.Client(timeout=60, auth=auth) as client:
        r = client.get(url)
        ct = r.headers.get("content-type", "")
        return len(r.content or b""), ct


def summarize_item(base: str, auth, entity: str, item: dict) -> dict:
    ref = item.get("Ref_Key", "")
    b64 = item.get("ФайлХранилище_Base64Data") or ""
    decoded_len = len(base64.b64decode(b64)) if b64 else 0
    stream_len, stream_ct = stream_bytes(base, auth, entity, ref)
    return {
        "ref_key": ref,
        "fields": {k: item.get(k) for k in KEY_FIELDS if k in item},
        "b64_len": len(b64),
        "decoded_len": decoded_len,
        "stream_len": stream_len,
        "stream_ct": stream_ct,
    }


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    settings = get_settings()
    base = settings.odata_base_url.rstrip("/") + "/"
    auth = (settings.odata_username, settings.odata_password)
    fm = load_attached_file_field_map()
    entity = fm["entity"]

    report: dict = {"entity": entity, "docs": []}
    for doc_num, owner, label in DOCS:
        items = fetch_files(base, auth, entity, owner)
        eml_items = [i for i in items if (i.get("Description") or "") == EML_DESC]
        entry = {
            "doc_number": doc_num,
            "owner_ref": owner,
            "label": label,
            "files_total": len(items),
            "eml_count": len(eml_items),
            "all_filenames": [
                f"{i.get('Description')}.{i.get('Расширение')}" for i in items
            ],
            "eml_records": [summarize_item(base, auth, entity, i) for i in eml_items],
        }
        report["docs"].append(entry)

    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
