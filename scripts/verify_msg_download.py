"""Download .msg from 1C OData and verify OLE/aspose checks."""
from __future__ import annotations

import io
import json
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_pochta.config import get_settings
from agent_pochta.services.odata_attached_file import (
    load_attached_file_field_map,
    read_attached_file_storage_bytes,
)
from agent_pochta.services.odata_client import ODataClient
from agent_pochta.services.odata_integration import resolve_attached_file_author_key

DOC_NUMBER = "НП00-003877"
DOC_REF = "fdb2cd68-8669-11f1-984a-6cb31113810e"
_MSK = ZoneInfo("Europe/Moscow")
OUT_DIR = ROOT / "data" / "verify_downloads"


def fetch_files(base: str, auth, owner: str, entity: str) -> list[dict]:
    flt = f"ВладелецФайла_Key eq guid'{owner}'"
    url = f"{base}{quote(entity)}?$format=json&$filter={quote(flt)}&$orderby=Ref_Key desc&$top=30"
    with httpx.Client(timeout=60, auth=auth) as client:
        response = client.get(url)
        response.raise_for_status()
        return response.json().get("value", [])


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    settings = get_settings()
    base = settings.odata_base_url.rstrip("/") + "/"
    auth = (settings.odata_username, settings.odata_password) if settings.odata_username else None
    client = ODataClient(
        settings.odata_base_url,
        username=settings.odata_username,
        password=settings.odata_password,
        timeout_sec=settings.odata_timeout_sec,
    )
    field_map = load_attached_file_field_map()
    entity = field_map["entity"]
    author_key = resolve_attached_file_author_key(
        explicit_key=settings.odata_file_author_key,
        incoming_defaults_file=settings.odata_incoming_defaults_file,
    )

    items = fetch_files(base, auth, DOC_REF, entity)
    msg_items = [
        item
        for item in items
        if (item.get("Description") or "") == DOC_NUMBER
        and (item.get("Расширение") or "").lower() == "msg"
    ]
    eml_items = [
        item
        for item in items
        if (item.get("Description") or "") == DOC_NUMBER
        and (item.get("Расширение") or "").lower() == "eml"
    ]

    latest = msg_items[0] if msg_items else (eml_items[0] if eml_items else None)
    if latest is None:
        listing = [
            {
                "Ref_Key": item.get("Ref_Key"),
                "Description": item.get("Description"),
                "Расширение": item.get("Расширение"),
                "ДатаСоздания": item.get("ДатаСоздания"),
                "Размер": item.get("Размер"),
            }
            for item in items
        ]
        print(
            json.dumps(
                {"error": "no matching attachment", "total": len(items), "files": listing},
                ensure_ascii=False,
                indent=2,
            )
        )
        sys.exit(2)

    ref_key = latest.get("Ref_Key")
    stored = read_attached_file_storage_bytes(
        client,
        entity=entity,
        ref_key=ref_key,
        field_map=field_map,
    )
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / f"{DOC_NUMBER}.msg"
    out_path.write_bytes(stored)

    now_msk = datetime.now(_MSK).replace(microsecond=0)
    created = str(latest.get("ДатаСоздания") or "")
    try:
        dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_MSK)
        dt_msk = dt.astimezone(_MSK).replace(microsecond=0)
        date_ok = abs((now_msk - dt_msk).total_seconds()) <= 3600 and not created.startswith("0001")
    except ValueError:
        date_ok = False

    checks = {
        "ole_magic": stored[:4] == bytes.fromhex("D0CF11E0"),
        "description": latest.get("Description") == DOC_NUMBER,
        "extension_msg": (latest.get("Расширение") or "").lower() == "msg",
        "date_msk_recent": date_ok,
        "author": (not author_key) or latest.get("Автор_Key") == author_key,
        "size_gt0": len(stored) > 0,
    }

    subject = from_addr = body_snip = parse_err = None
    try:
        from aspose.email import MailMessage
        from aspose.email.storage import MsgLoadOptions

        msg = MailMessage.load(io.BytesIO(stored), MsgLoadOptions())
        subject = str(msg.subject or "")
        from_addr = str(getattr(msg, "from_address", None) or getattr(msg, "sender", None) or "")
        body = str(getattr(msg, "body", None) or getattr(msg, "html_body", None) or "")
        body_snip = body[:200].replace("\r", " ").replace("\n", " ")
        checks["aspose_parse"] = bool(subject or from_addr or body_snip)
    except Exception as exc:
        parse_err = str(exc)
        checks["aspose_parse"] = False

    report = {
        "file": str(out_path),
        "size_bytes": len(stored),
        "magic_hex": stored[:4].hex(" "),
        "ref_key": ref_key,
        "Description": latest.get("Description"),
        "Расширение": latest.get("Расширение"),
        "ДатаСоздания": created,
        "Автор_Key": latest.get("Автор_Key"),
        "expected_author": author_key,
        "now_msk": now_msk.isoformat(),
        "subject": subject,
        "from": from_addr,
        "body_snippet": body_snip,
        "parse_err": parse_err,
        "msg_count": len(msg_items),
        "eml_count": len(eml_items),
        "checks": checks,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
