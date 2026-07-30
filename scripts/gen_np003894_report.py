"""Generate np003894 report JSON with OData retry and cached fallbacks."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
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
COMPARE = {
    "760-agent": ("АЛ00-000760", "20dbfa4d-8689-11f1-984a-6cb31113810e"),
    "762-agent": ("АЛ00-000762", "18516943-871f-11f1-984b-6cb31113810e"),
}
DOC_ENTITY = "Document_ТД_ВходящаяКорреспонденция"
BINARY_SKIP = {"ФайлХранилище_Base64Data", "ТекстХранилище", "ФайлХранилище"}


def clean(item: dict) -> dict:
    return {
        k: v
        for k, v in item.items()
        if k not in BINARY_SKIP and not str(k).endswith("@navigationLinkUrl")
    }


def try_find_doc(base: str, auth, number: str) -> tuple[list[dict], dict | None]:
    flt = f"Number eq '{number}'"
    url = (
        f"{base}{quote(DOC_ENTITY)}?$format=json"
        f"&$filter={quote(flt)}&$orderby=Date desc&$top=5"
    )
    try:
        r = httpx.get(url, auth=auth, timeout=120)
        err = None if r.status_code < 400 else {
            "status": r.status_code,
            "body": r.text[:500],
        }
        items = r.json().get("value", []) if r.status_code < 400 else []
        return items, err
    except Exception as exc:
        return [], {"exception": str(exc)}


def try_attachments(base: str, auth, entity: str, owner: str) -> tuple[list[dict], dict | None]:
    flt = f"ВладелецФайла_Key eq guid'{owner}'"
    url = f"{base}{quote(entity)}?$format=json&$filter={quote(flt)}&$orderby=ДатаСоздания desc"
    try:
        r = httpx.get(url, auth=auth, timeout=120)
        err = None if r.status_code < 400 else {
            "status": r.status_code,
            "body": r.text[:500],
        }
        items = r.json().get("value", []) if r.status_code < 400 else []
        return items, err
    except Exception as exc:
        return [], {"exception": str(exc)}


def analyze_item(
    base: str,
    auth,
    client: ODataClient,
    entity: str,
    field_map: dict,
    item: dict,
) -> dict:
    ref = item.get("Ref_Key", "")
    full_url = f"{base}{quote(entity)}(guid'{ref}')?$format=json"
    full = item
    full_err = None
    if ref:
        r = httpx.get(full_url, auth=auth, timeout=120)
        if r.status_code < 400:
            full = r.json()
        else:
            full_err = {"status": r.status_code, "body": r.text[:500]}
    fields = clean(full)
    storage_bytes = (
        read_attached_file_storage_bytes(
            client, entity=entity, ref_key=ref, field_map=field_map
        )
        if ref
        else b""
    )
    stream_url = f"{base}{quote(entity)}(guid'{ref}')/ФайлХранилище"
    sr = httpx.get(stream_url, auth=auth, timeout=120) if ref else None
    stream_bytes = sr.content or b"" if sr else b""
    ext = (fields.get("Расширение") or "").strip()
    desc = (fields.get("Description") or "").strip()
    return {
        "ref_key": ref,
        "filename": f"{desc}.{ext}" if ext else desc,
        "fields": fields,
        "full_record_error": full_err,
        "storage": {
            "meta_size": fields.get("Размер"),
            "storage_reader_len": len(storage_bytes),
            "stream_get_len": len(stream_bytes),
            "stream_status": sr.status_code if sr else None,
            "stream_content_type": sr.headers.get("content-type") if sr else None,
            "magic_hex": stream_bytes[:8].hex() if stream_bytes else "",
        },
    }


def load_cached_agent_compare() -> dict:
    path = ROOT / "data" / "temp" / "attach_field_report.json"
    if not path.is_file():
        return {}
    raw = path.read_bytes()
    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        text = raw.decode("utf-16")
    else:
        text = raw.decode("utf-8-sig")
    data = json.loads(text)
    rows = data.get("rows") or []
    out = {}
    for label, col in [("760-agent", "760"), ("762-agent", "762_latest")]:
        out[label] = {
            row.get("odata"): row.get(col)
            for row in rows
            if row.get("odata") and not str(row.get("odata", "")).endswith("@navigationLinkUrl")
        }
    out["760_attachment_ref"] = data.get("760_attachments")
    out["762_attachment_ref"] = data.get("762_attachments")
    out["stream_bytes_cache"] = data.get("stream_bytes")
    out["cached_at_note"] = "attach_field_report.json from earlier OData session (~24.07.2026 10:56 MSK)"
    return out


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
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "target_doc_number": TARGET,
        "odata_access": {},
    }

    docs, doc_err = try_find_doc(base, auth, TARGET)
    report["odata_access"]["find_document"] = doc_err or {"status": 200, "count": len(docs)}
    report["target_doc_search"] = [
        {
            "Ref_Key": d.get("Ref_Key"),
            "Number": d.get("Number"),
            "Date": d.get("Date"),
            "Posted": d.get("Posted"),
        }
        for d in docs
    ]

    if docs:
        owner = docs[0]["Ref_Key"]
        report["target_document"] = clean(docs[0])
        items, att_err = try_attachments(base, auth, attach_entity, owner)
        report["odata_access"]["list_attachments"] = att_err or {
            "status": 200,
            "count": len(items),
        }
        report["attachments"] = [
            analyze_item(base, auth, client, attach_entity, field_map, i) for i in items
        ]
    else:
        report["attachments"] = []
        report["odata_access"]["note"] = (
            "Live OData read failed. НП00-003894 not fetched. "
            "User odata.user gets HTTP 401 on Document/Attachments collection "
            "and HTTP 500 'Поле объекта заблокировано' on direct GET by known refs."
        )

    compare_live: dict = {}
    for label, (number, owner) in COMPARE.items():
        items, err = try_attachments(base, auth, attach_entity, owner)
        compare_live[label] = {
            "document_number": number,
            "owner_ref": owner,
            "odata_error": err,
            "attachments_count": len(items),
            "filenames": [
                f"{(i.get('Description') or '')}.{(i.get('Расширение') or '')}".rstrip(".")
                for i in items
            ],
        }
        if items:
            pick = next(
                (i for i in items if (i.get("Description") or "") == number),
                items[0],
            )
            compare_live[label]["primary"] = analyze_item(
                base, auth, client, attach_entity, field_map, pick
            )

    report["compare_agent_live"] = compare_live
    report["compare_agent_cached"] = load_cached_agent_compare()
    report["manual_storage_reference"] = {
        "source": "debug_ud.txt + prior OData sessions",
        "human_manual_pattern": {
            "ТипХраненияФайла": "ВТомахНаДиске",
            "Том_Key": "21886495-364e-11ea-82f2-ac1f6b05524c",
            "ПутьКФайлу": "YYYYMMDD\\<имя>.msg (пример: 20180417\\МГ00-0056.msg)",
            "note": "Ручные файлы в 1С часто в томе, не в ИБ",
        },
        "agent_current_pattern": {
            "ТипХраненияФайла": "ВИнформационнойБазе",
            "Том_Key": "00000000-0000-0000-0000-000000000000",
            "ПутьКФайлу": "",
            "ФайлХранилище_Type": "application/octet-stream",
        },
    }

    out_path = ROOT / "data" / "temp" / "np003894_manual_attach_report.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"saved": str(out_path), "target_found": bool(docs)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
