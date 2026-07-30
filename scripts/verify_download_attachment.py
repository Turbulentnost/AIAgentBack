"""Download attached file from 1C OData and verify metadata/content."""
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
_OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_EMPTY_GUID = "00000000-0000-0000-0000-000000000000"

DOCS = [
    ("НП00-003877", "fdb2cd68-8669-11f1-984a-6cb31113810e"),
    ("НП00-003878", "775a0efd-866b-11f1-984a-6cb31113810e"),
    ("НП00-003876", "e9e1b18c-8669-11f1-984a-6cb31113810e"),
    ("НП00-003870", "ccb7ab6d-8653-11f1-984a-6cb31113810e"),
]


def fetch_files(base: str, auth, entity: str, owner: str) -> list[dict]:
    flt = f"ВладелецФайла_Key eq guid'{owner}'"
    url = f"{base}{quote(entity)}?$format=json&$filter={quote(flt)}&$orderby=Ref_Key desc&$top=50"
    with httpx.Client(timeout=120, auth=auth) as client:
        r = client.get(url)
        r.raise_for_status()
        return r.json().get("value", [])


def pick_target_file(items: list[dict], doc_number: str) -> dict | None:
    """Prefer doc-number named file (.msg/.eml), else legacy Входящее_письмо."""
    preferred_names = {doc_number, f"{doc_number}.msg", f"{doc_number}.eml", "Входящее_письмо"}
    for item in items:
        desc = (item.get("Description") or "").strip()
        ext = (item.get("Расширение") or "").strip().lower()
        full = f"{desc}.{ext}" if ext else desc
        if desc in preferred_names or full in preferred_names:
            return item
    legacy = [i for i in items if (i.get("Description") or "") == "Входящее_письмо"]
    return legacy[0] if legacy else (items[0] if items else None)


def parse_email_fields(content: bytes, ext: str) -> dict:
    if ext == "msg" and is_msg_bytes(content):
        try:
            from aspose.email_foss import msg as msgmod

            with Path("/tmp/_verify.msg").open("wb") as f:
                f.write(content)
            m = msgmod.MapiMessage.load("/tmp/_verify.msg")
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
            "format": "eml/rfc822",
            "subject": msg.get("Subject", ""),
            "from": msg.get("From", ""),
            "date": msg.get("Date", ""),
        }
    except Exception as exc:
        return {"format": ext or "unknown", "parse_error": str(exc)}


def verify_item(
    *,
    doc_number: str,
    owner_ref: str,
    item: dict,
    content: bytes,
    processed_at: datetime | None,
) -> dict:
    desc = (item.get("Description") or "").strip()
    ext = (item.get("Расширение") or "").strip().lower()
    created = item.get("ДатаСоздания") or ""
    author = item.get("Автор_Key") or _EMPTY_GUID
    editor = item.get("Редактировал_Key") or _EMPTY_GUID
    size_meta = int(item.get("Размер") or 0)

    checks: dict[str, bool | str] = {}
    checks["description_is_doc_number"] = desc == doc_number
    checks["extension_msg_or_eml"] = ext in {"msg", "eml"}
    checks["size_nonzero"] = len(content) > 0
    checks["size_matches_meta"] = size_meta == 0 or size_meta == len(content)

    if ext == "msg":
        checks["ole_magic"] = content[:8] == _OLE_MAGIC
    elif ext == "eml":
        head = content[:4096].decode("utf-8", errors="replace")
        checks["has_rfc822_headers"] = "Subject:" in head or "From:" in head

    if processed_at and created and not str(created).startswith("0001"):
        proc_msk = processed_at.astimezone(_MSK).replace(microsecond=0, tzinfo=None)
        try:
            created_dt = datetime.fromisoformat(str(created).split(".")[0])
            delta_sec = abs((created_dt - proc_msk).total_seconds())
            checks["created_at_near_processed_msk"] = delta_sec <= 180
            checks["created_at_delta_sec"] = str(int(delta_sec))
        except ValueError:
            checks["created_at_near_processed_msk"] = False

    checks["author_set"] = author != _EMPTY_GUID
    checks["editor_set"] = editor != _EMPTY_GUID

    return {
        "ref_key": item.get("Ref_Key"),
        "description": desc,
        "extension": ext,
        "full_name": f"{desc}.{ext}" if ext else desc,
        "size_meta": size_meta,
        "size_bytes": len(content),
        "storage_kind": item.get("ТипХраненияФайла"),
        "created_at": created,
        "modified_utc": item.get("ДатаМодификацияУниверсальная") or item.get("ДатаМодификацииУниверсальная"),
        "author_key": author,
        "editor_key": editor,
        "hex_first16": content[:16].hex(),
        "magic": content[:8].hex(),
        "checks": checks,
        "email_fields": parse_email_fields(content, ext),
    }


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser()
    parser.add_argument("--doc", default="НП00-003877")
    parser.add_argument("--save-dir", default=str(ROOT / "data" / "temp" / "verify_attach"))
    args = parser.parse_args()

    doc_number = args.doc
    owner_ref = next((ref for num, ref in DOCS if num == doc_number), None)
    if not owner_ref:
        raise SystemExit(f"Unknown doc {doc_number!r}; known: {[d[0] for d in DOCS]}")

    settings = get_settings()
    base = settings.odata_base_url.rstrip("/") + "/"
    auth = (settings.odata_username, settings.odata_password)
    fm = load_attached_file_field_map()
    entity = fm["entity"]
    client = ODataClient(
        settings.odata_base_url,
        username=settings.odata_username,
        password=settings.odata_password,
        timeout_sec=settings.odata_timeout_sec,
    )

    items = fetch_files(base, auth, entity, owner_ref)
    target = pick_target_file(items, doc_number)
    if not target:
        print(json.dumps({"doc": doc_number, "error": "no attached files"}, ensure_ascii=False, indent=2))
        raise SystemExit(1)

    ref = target["Ref_Key"]
    content = read_attached_file_storage_bytes(client, entity=entity, ref_key=ref, field_map=fm)
    if not content:
        b64 = target.get("ФайлХранилище_Base64Data") or ""
        content = base64.b64decode(b64) if b64 else b""

    save_dir = Path(args.save_dir)
    save_dir.mkdir(parents=True, exist_ok=True)
    ext = (target.get("Расширение") or "bin").strip().lower()
    out_path = save_dir / f"{doc_number}.{ext}"
    out_path.write_bytes(content)

    report = {
        "doc_number": doc_number,
        "owner_ref": owner_ref,
        "files_total": len(items),
        "all_files": [f"{i.get('Description')}.{i.get('Расширение')}" for i in items],
        "downloaded_path": str(out_path),
        "target": verify_item(
            doc_number=doc_number,
            owner_ref=owner_ref,
            item=target,
            content=content,
            processed_at=None,
        ),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
