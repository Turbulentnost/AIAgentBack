"""Diagnostic: НП00-003884 attachment state in DB and OData."""
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
from agent_pochta.db.models import EmailMessageRow  # noqa: E402
from agent_pochta.db.session import get_session_factory  # noqa: E402
from agent_pochta.services.odata_attached_file import (  # noqa: E402
    load_attached_file_field_map,
    read_attached_file_storage_bytes,
)
from agent_pochta.services.odata_client import ODataClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

DOC = "НП00-003884"
_OLE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_EMPTY = "00000000-0000-0000-0000-000000000000"


def db_row() -> dict:
    factory = get_session_factory()
    with factory() as session:
        row = session.scalar(
            select(EmailMessageRow).where(EmailMessageRow.erp_document_number == DOC)
        )
        if row is None:
            return {"error": "not_in_db"}
        payload = {}
        if row.raw_payload_json:
            payload = json.loads(row.raw_payload_json)
        return {
            "id": str(row.id),
            "message_id": row.message_id,
            "erp_task_id": row.erp_task_id,
            "erp_document_number": row.erp_document_number,
            "processed_at": str(row.processed_at),
            "status": row.status,
            "erp_attachments": payload.get("erp_attachments"),
            "erp_sync_errors": payload.get("erp_sync_errors"),
            "erp_attach_error": payload.get("erp_attach_error"),
        }


def odata_report(owner_ref: str, target_ref: str | None) -> dict:
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

    flt = f"ВладелецФайла_Key eq guid'{owner_ref}'"
    url = f"{base}{quote(entity)}?$format=json&$filter={quote(flt)}&$orderby=Ref_Key desc&$top=50"
    with httpx.Client(timeout=120, auth=auth) as hc:
        items = hc.get(url).raise_for_status().json().get("value", [])

    target = None
    if target_ref:
        target = next((i for i in items if i.get("Ref_Key") == target_ref), None)
    if target is None:
        target = next(
            (i for i in items if (i.get("Description") or "").strip() == DOC),
            items[0] if items else {},
        )

    ref = target.get("Ref_Key", "")
    stream_len = 0
    stream_ct = ""
    stream_magic = ""
    if ref:
        stream_url = f"{base}{quote(entity)}(guid'{ref}')/ФайлХранилище"
        with httpx.Client(timeout=120, auth=auth) as hc:
            sr = hc.get(stream_url)
            body = sr.content or b""
            stream_len = len(body)
            stream_ct = sr.headers.get("content-type", "")
            stream_magic = body[:8].hex()

    content = read_attached_file_storage_bytes(
        client, entity=entity, ref_key=ref, field_map=fm
    ) if ref else b""
    b64 = target.get("ФайлХранилище_Base64Data") or ""
    b64_decoded = base64.b64decode(b64) if b64 else b""

    return {
        "owner_ref": owner_ref,
        "files_total": len(items),
        "all_files": [
            f"{i.get('Description')}.{i.get('Расширение')}" for i in items
        ],
        "target": {
            "Ref_Key": target.get("Ref_Key"),
            "Description": target.get("Description"),
            "Расширение": target.get("Расширение"),
            "Размер": target.get("Размер"),
            "ТипХраненияФайла": target.get("ТипХраненияФайла"),
            "ФайлХранилище_Type": target.get("ФайлХранилище_Type"),
            "ДатаСоздания": target.get("ДатаСоздания"),
            "Автор_Key": target.get("Автор_Key"),
            "Редактировал_Key": target.get("Редактировал_Key"),
            "Том_Key": target.get("Том_Key"),
        },
        "stream_direct_len": stream_len,
        "stream_direct_ct": stream_ct,
        "stream_direct_magic": stream_magic,
        "read_storage_len": len(content or b""),
        "read_storage_magic": (content or b"")[:8].hex(),
        "b64_field_len": len(b64),
        "b64_decoded_len": len(b64_decoded),
        "ole_magic_ok": (content or b"")[:8] == _OLE or stream_magic == _OLE.hex(),
        "author_empty": (target.get("Автор_Key") or _EMPTY) == _EMPTY,
    }


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    db = db_row()
    owner = db.get("erp_task_id") or ""
    attach_ref = None
    atts = db.get("erp_attachments") or []
    if atts:
        attach_ref = atts[0].get("ref_key")
    odata = odata_report(owner, attach_ref) if owner else {"error": "no_owner_ref"}

    print(json.dumps({"db": db, "odata": odata}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
