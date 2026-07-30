"""Read-only OData report: human-filled НП00-003894 attachments vs agent docs."""
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
from agent_pochta.services.odata_attached_file import (  # noqa: E402
    load_attached_file_field_map,
    read_attached_file_storage_bytes,
)
from agent_pochta.services.odata_client import ODataClient  # noqa: E402

TARGET = "НП00-003894"
COMPARE_DOCS = {
    "760-agent": "АЛ00-000760",
    "762-agent": "АЛ00-000762",
}
DOC_ENTITY = "Document_ТД_ВходящаяКорреспонденция"
SKIP_COMPARE = {
    "Ref_Key",
    "Description",
    "Расширение",
    "Размер",
    "ДатаСоздания",
    "ДатаМодификацииУниверсальная",
    "ВладелецФайла_Key",
    "ДатаЗаема",
}
BINARY_SKIP = {"ФайлХранилище_Base64Data", "ТекстХранилище", "ФайлХранилище"}


def clean_record(item: dict) -> dict:
    return {
        k: v
        for k, v in item.items()
        if k not in BINARY_SKIP and not str(k).endswith("@navigationLinkUrl")
    }


def find_docs(base: str, auth, number: str) -> list[dict]:
    flt = f"Number eq '{number}'"
    url = (
        f"{base}{quote(DOC_ENTITY)}?$format=json"
        f"&$filter={quote(flt)}&$orderby=Date desc&$top=5"
    )
    return httpx.get(url, auth=auth, timeout=120).raise_for_status().json().get("value", [])


def fetch_attachments(base: str, auth, entity: str, owner: str) -> list[dict]:
    flt = f"ВладелецФайла_Key eq guid'{owner}'"
    url = (
        f"{base}{quote(entity)}?$format=json"
        f"&$filter={quote(flt)}&$orderby=ДатаСоздания desc"
    )
    return httpx.get(url, auth=auth, timeout=120).raise_for_status().json().get("value", [])


def fetch_full_record(base: str, auth, entity: str, ref: str) -> dict:
    url = f"{base}{quote(entity)}(guid'{ref}')?$format=json"
    return httpx.get(url, auth=auth, timeout=120).raise_for_status().json()


def analyze_attachment(
    base: str,
    auth,
    client: ODataClient,
    entity: str,
    field_map: dict,
    item: dict,
) -> dict:
    ref = item.get("Ref_Key", "")
    full = fetch_full_record(base, auth, entity, ref) if ref else item
    fields = clean_record(full)
    b64 = full.get("ФайлХранилище_Base64Data") or ""
    decoded_len = len(base64.b64decode(b64)) if b64 else 0
    storage_bytes = (
        read_attached_file_storage_bytes(
            client, entity=entity, ref_key=ref, field_map=field_map
        )
        if ref
        else b""
    )
    stream_url = f"{base}{quote(entity)}(guid'{ref}')/ФайлХранилище"
    sr = httpx.get(stream_url, auth=auth, timeout=120)
    stream_bytes = sr.content or b""
    ext = (fields.get("Расширение") or "").strip()
    desc = (fields.get("Description") or "").strip()
    fname = f"{desc}.{ext}" if ext else desc
    meta_size = fields.get("Размер")
    return {
        "ref_key": ref,
        "filename": fname,
        "fields": fields,
        "storage": {
            "meta_size": meta_size,
            "b64_present": bool(b64),
            "b64_char_len": len(b64),
            "b64_decoded_len": decoded_len,
            "storage_reader_len": len(storage_bytes),
            "stream_get_len": len(stream_bytes),
            "stream_status": sr.status_code,
            "stream_content_type": sr.headers.get("content-type"),
            "size_matches_meta": meta_size == len(stream_bytes)
            if meta_size is not None
            else None,
            "magic_hex": stream_bytes[:8].hex() if stream_bytes else "",
        },
    }


def pick_primary_attachment(items: list[dict]) -> dict | None:
    if not items:
        return None
    for item in items:
        if (item.get("Description") or "") == "Входящее_письмо":
            return item
    return items[0]


def compare_records(records: dict[str, dict]) -> dict:
    all_keys: set[str] = set()
    for rec in records.values():
        all_keys.update(rec.keys())
    diffs: dict[str, dict] = {}
    for key in sorted(all_keys):
        if key in SKIP_COMPARE:
            continue
        vals = {label: records[label].get(key) for label in records}
        uniq = {
            json.dumps(v, ensure_ascii=False, sort_keys=True, default=str)
            for v in vals.values()
        }
        if len(uniq) > 1:
            diffs[key] = vals
    return diffs


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    settings = get_settings()
    base = settings.odata_base_url.rstrip("/") + "/"
    auth = (settings.odata_username, settings.odata_password)
    field_map = load_attached_file_field_map()
    attach_entity = field_map["entity"]
    client = ODataClient(
        settings.odata_base_url,
        username=settings.odata_username,
        password=settings.odata_password,
        timeout_sec=120,
    )

    report: dict = {
        "generated_read_only": True,
        "target_doc_number": TARGET,
    }

    docs_found = find_docs(base, auth, TARGET)
    report["target_doc_search"] = [
        {
            "Ref_Key": d.get("Ref_Key"),
            "Number": d.get("Number"),
            "Date": d.get("Date"),
            "Posted": d.get("Posted"),
            "DeletionMark": d.get("DeletionMark"),
        }
        for d in docs_found
    ]
    if not docs_found:
        raise SystemExit(f"Document not found: {TARGET}")

    target_doc = docs_found[0]
    owner = target_doc["Ref_Key"]
    report["target_document"] = clean_record(target_doc)

    items = fetch_attachments(base, auth, attach_entity, owner)
    report["attachments_summary"] = {
        "count": len(items),
        "filenames": [
            f"{(i.get('Description') or '')}.{(i.get('Расширение') or '')}".rstrip(".")
            for i in items
        ],
    }
    report["attachments"] = [
        analyze_attachment(base, auth, client, attach_entity, field_map, i)
        for i in items
    ]

    compare_attach_refs: dict[str, str] = {}
    primary = pick_primary_attachment(items)
    if primary:
        compare_attach_refs["np003894-manual"] = primary["Ref_Key"]

    for label, number in COMPARE_DOCS.items():
        found = find_docs(base, auth, number)
        report[f"compare_doc_{label}"] = [
            {
                "Ref_Key": d.get("Ref_Key"),
                "Number": d.get("Number"),
                "Date": d.get("Date"),
            }
            for d in found[:3]
        ]
        if not found:
            continue
        att_items = fetch_attachments(base, auth, attach_entity, found[0]["Ref_Key"])
        report[f"compare_attachments_{label}"] = {
            "owner_ref": found[0]["Ref_Key"],
            "count": len(att_items),
            "filenames": [
                f"{(a.get('Description') or '')}.{(a.get('Расширение') or '')}".rstrip(".")
                for a in att_items
            ],
        }
        pick = pick_primary_attachment(att_items)
        if pick:
            compare_attach_refs[label] = pick["Ref_Key"]

    if len(compare_attach_refs) >= 2:
        records = {
            label: clean_record(
                fetch_full_record(base, auth, attach_entity, ref)
            )
            for label, ref in compare_attach_refs.items()
        }
        report["comparison"] = {
            "attachment_refs": compare_attach_refs,
            "field_value_diffs_vs_manual": compare_records(records),
            "manual_reference_fields": records.get("np003894-manual", {}),
            "agent_samples": {
                k: v for k, v in records.items() if k != "np003894-manual"
            },
        }

    out_path = ROOT / "data" / "temp" / "np003894_manual_attach_report.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"saved": str(out_path), "attachments": report["attachments_summary"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
