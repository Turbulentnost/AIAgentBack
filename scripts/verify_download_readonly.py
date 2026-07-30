"""Read-only verification of 1C attached email file (no writes)."""
from __future__ import annotations

import argparse
import base64
import json
import sys
from datetime import datetime
from email import policy
from email.parser import BytesParser
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_pochta.config import get_settings  # noqa: E402
from agent_pochta.services.email_msg import is_msg_bytes  # noqa: E402
from agent_pochta.services.odata_attached_file import (  # noqa: E402
    load_attached_file_field_map,
    read_attached_file_storage_bytes,
)
from agent_pochta.services.odata_client import ODataClient  # noqa: E402

_MSK = ZoneInfo("Europe/Moscow")
_OLE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_EMPTY = "00000000-0000-0000-0000-000000000000"
DOC_ENTITY = "Document_ТД_ВходящаяКорреспонденция"
FILE_ENTITY = "Catalog_ТД_ВходящаяКорреспонденцияПрисоединенныеФайлы"


def fetch_doc(base: str, auth, ref: str) -> dict:
    url = f"{base}{quote(DOC_ENTITY)}(guid'{ref}')?$format=json"
    r = httpx.get(url, auth=auth, timeout=60)
    r.raise_for_status()
    return r.json()


def fetch_files(base: str, auth, owner: str) -> list[dict]:
    flt = f"ВладелецФайла_Key eq guid'{owner}'"
    url = f"{base}{quote(FILE_ENTITY)}?$format=json&$filter={quote(flt)}&$orderby=Ref_Key desc&$top=50"
    r = httpx.get(url, auth=auth, timeout=120)
    r.raise_for_status()
    return r.json().get("value", [])


def parse_inner(content: bytes, ext: str) -> dict:
    if ext == "msg" and is_msg_bytes(content):
        try:
            tmp = Path("/tmp/_v.msg")
            tmp.write_bytes(content)
            from aspose.email_foss import msg as msgmod

            m = msgmod.MapiMessage.load(str(tmp))
            return {
                "format": "msg",
                "subject": m.subject or "",
                "from": str(m.sender_email_address or m.sender_name or ""),
                "date": str(m.delivery_time or m.client_submit_time or ""),
            }
        except Exception as exc:
            return {"format": "msg", "parse_error": str(exc)}
    try:
        msg = BytesParser(policy=policy.default).parsebytes(content)
        return {
            "format": "eml",
            "subject": msg.get("Subject", ""),
            "from": msg.get("From", ""),
            "date": msg.get("Date", ""),
        }
    except Exception as exc:
        return {"format": ext, "parse_error": str(exc)}


def pick_email_file(items: list[dict], doc_number: str) -> dict | None:
    for key in (doc_number, "Входящее_письмо"):
        for item in items:
            if (item.get("Description") or "").strip() == key:
                return item
    for item in items:
        ext = (item.get("Расширение") or "").lower()
        if ext in {"msg", "eml"}:
            return item
    return None


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    p = argparse.ArgumentParser()
    p.add_argument("--doc-number", default="НП00-003877")
    p.add_argument("--doc-ref", default="fdb2cd68-8669-11f1-984a-6cb31113810e")
    p.add_argument("--processed-at", default="2026-07-23T10:42:24")
    p.add_argument("--out-dir", default=str(ROOT / "data" / "temp" / "verify_attach"))
    args = p.parse_args()

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

    doc = fetch_doc(base, auth, args.doc_ref)
    items = fetch_files(base, auth, args.doc_ref)
    target = pick_email_file(items, args.doc_number)
    if not target:
        print(json.dumps({"error": "no email attachment found", "all_files": items}, ensure_ascii=False, indent=2))
        return

    ref = target["Ref_Key"]
    content = read_attached_file_storage_bytes(client, entity=FILE_ENTITY, ref_key=ref, field_map=fm)
    if not content:
        b64 = target.get("ФайлХранилище_Base64Data") or ""
        content = base64.b64decode(b64) if b64 else b""

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ext = (target.get("Расширение") or "bin").lower()
    out_path = out_dir / f"{args.doc_number}_downloaded.{ext}"
    out_path.write_bytes(content)

    desc = (target.get("Description") or "").strip()
    created = str(target.get("ДатаСоздания") or "")
    doc_date = str(doc.get("Date") or doc.get("Дата") or "")
    proc = datetime.fromisoformat(args.processed_at).replace(tzinfo=_MSK)
    created_dt = datetime.fromisoformat(created.split(".")[0]) if created and not created.startswith("0001") else None

    checks = {
        "filename_is_doc_number": desc == args.doc_number,
        "extension_msg_or_eml": ext in {"msg", "eml"},
        "bytes_nonzero": len(content) > 0,
        "ole_magic_if_msg": (content[:8] == _OLE) if ext == "msg" else None,
        "rfc822_headers_if_eml": (b"Subject:" in content[:4096] or b"From:" in content[:4096]) if ext == "eml" else None,
        "author_set": (target.get("Автор_Key") or _EMPTY) != _EMPTY,
        "editor_set": (target.get("Редактировал_Key") or _EMPTY) != _EMPTY,
    }
    if created_dt:
        checks["created_at_near_processed_msk_3min"] = abs((created_dt - proc.replace(tzinfo=None)).total_seconds()) <= 180
        checks["created_at_delta_from_processed_sec"] = int(abs((created_dt - proc.replace(tzinfo=None)).total_seconds()))

    report = {
        "doc_number": args.doc_number,
        "doc_ref": args.doc_ref,
        "doc_date_1c": doc_date,
        "processed_at_msk_expected": args.processed_at,
        "files_total": len(items),
        "all_files": [f"{i.get('Description')}.{i.get('Расширение')}" for i in items],
        "downloaded_path": str(out_path),
        "metadata": {
            "ref_key": ref,
            "description": desc,
            "extension": ext,
            "full_name": f"{desc}.{ext}",
            "size_meta": target.get("Размер"),
            "size_bytes": len(content),
            "storage_kind": target.get("ТипХраненияФайла"),
            "created_at": created,
            "modified_utc": target.get("ДатаМодификацииУниверсальная"),
            "author_key": target.get("Автор_Key"),
            "editor_key": target.get("Редактировал_Key"),
        },
        "hex_first16": content[:16].hex(),
        "magic_first8": content[:8].hex(),
        "inner_email": parse_inner(content, ext),
        "checks": checks,
        "all_pass": all(v is True for v in checks.values() if isinstance(v, bool)),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
