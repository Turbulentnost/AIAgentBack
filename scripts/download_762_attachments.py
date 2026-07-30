"""Download ALL attachments for АЛ00-000762 from 1C OData into data/temp/download_762/."""
from __future__ import annotations

import base64
import json
import re
import sys
from email import policy
from email.parser import BytesParser
from pathlib import Path
from urllib.parse import quote

import httpx
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_pochta.config import get_settings  # noqa: E402
from agent_pochta.schemas import EmailMessage  # noqa: E402
from agent_pochta.services.email_msg import (  # noqa: E402
    eml_bytes_to_msg_bytes,
    normalize_attachment_filename,
)
from agent_pochta.services.erp_attachments import ensure_full_email_bytes_for_erp  # noqa: E402
from agent_pochta.services.odata_attached_file import load_attached_file_field_map  # noqa: E402
from agent_pochta.services.odata_client import ODataClient  # noqa: E402
from agent_pochta.services.vault import StubVaultClient  # noqa: E402

DOC_NUMBER = "АЛ00-000762"
OWNER_REF = "18516943-871f-11f1-984b-6cb31113810e"
SAVE_DIR = ROOT / "data" / "temp" / "download_762"
_OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_META_KEYS = (
    "Ref_Key",
    "Description",
    "Расширение",
    "Размер",
    "ТипХраненияФайла",
    "ПутьКФайлу",
    "ФайлХранилище_Type",
    "Том_Key",
    "Автор_Key",
    "Изменил_Key",
    "Редактирует_Key",
    "ДатаСоздания",
    "DeletionMark",
)


def safe_filename(desc: str, ext: str, ref: str) -> str:
    name = f"{desc}.{ext}" if ext else desc
    name = name.strip() or ref[:8]
    name = re.sub(r'[<>:"/\\|?*]', "_", name)
    return name


def list_attachments(base: str, auth, entity: str, owner: str) -> list[dict]:
    flt = f"ВладелецФайла_Key eq guid'{owner}'"
    url = (
        f"{base}{quote(entity)}?$format=json"
        f"&$filter={quote(flt)}&$orderby=ДатаСоздания desc&$top=50"
    )
    return httpx.get(url, auth=auth, timeout=120).raise_for_status().json().get("value", [])


def slim_record(record: dict) -> dict:
    out: dict = {}
    for k in sorted(record.keys()):
        if k.startswith("@") or k.endswith("_Base64Data"):
            continue
        out[k] = record.get(k)
    return out


def verify_content(content: bytes, ext: str, meta_size: int) -> dict:
    checks: dict = {
        "size_bytes": len(content),
        "meta_size": meta_size,
        "size_matches_meta": meta_size == 0 or meta_size == len(content),
        "nonzero": len(content) > 0,
        "hex_first16": content[:16].hex() if content else "",
    }
    ext_l = ext.lower()
    if ext_l == "msg":
        checks["cfb_magic"] = content[:8] == _OLE_MAGIC
    elif ext_l == "pdf":
        checks["pdf_header"] = content[:5] == b"%PDF-"
    elif ext_l == "eml":
        head = content[:4096].decode("utf-8", errors="replace")
        checks["has_rfc822_headers"] = "Subject:" in head or "From:" in head
    return checks


def load_source_eml(settings) -> tuple[bytes, str]:
    compare_dir = ROOT / "data" / "temp" / "compare_762"
    candidates = sorted(
        p for p in compare_dir.glob("*000762*.eml") if "PROBE" not in p.name.upper()
    )
    if candidates:
        path = candidates[0]
        return path.read_bytes(), str(path.relative_to(ROOT))

    engine = create_engine(settings.database_url)
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT message_id, mailbox, sender_email, subject, received_at "
                "FROM email_messages WHERE erp_document_number = :doc "
                "ORDER BY id DESC LIMIT 1"
            ),
            {"doc": DOC_NUMBER},
        ).fetchone()
    if not row:
        return b"", "not_found"

    email = EmailMessage(
        message_id=row.message_id,
        mailbox=row.mailbox or "",
        sender_email=row.sender_email or "",
        subject=row.subject or "",
        received_at=row.received_at,
        attachments=[],
    )
    vault = StubVaultClient()
    eml_bytes = ensure_full_email_bytes_for_erp(email, vault)
    return eml_bytes, f"imap:{row.message_id}"


def build_agent_local_sources(eml_bytes: bytes, save_dir: Path) -> dict:
    """What agent would upload: MSG from EML + first PDF attachment."""
    out: dict = {"paths": {}, "sizes": {}}
    if not eml_bytes:
        out["error"] = "no source eml"
        return out

    msg_bytes = eml_bytes_to_msg_bytes(eml_bytes, embed_attachments=False)
    msg_path = save_dir / "agent_local_Заявка!.msg"
    msg_path.write_bytes(msg_bytes)
    out["paths"]["msg"] = str(msg_path.relative_to(ROOT))
    out["sizes"]["msg"] = len(msg_bytes)
    out["msg_cfb_magic"] = msg_bytes[:8] == _OLE_MAGIC

    msg = BytesParser(policy=policy.default).parsebytes(eml_bytes)
    for part in msg.walk():
        fn = part.get_filename()
        if not fn:
            continue
        payload = part.get_payload(decode=True) or b""
        if not payload:
            continue
        name = normalize_attachment_filename(fn) or fn
        pdf_path = save_dir / f"agent_local_{name}"
        pdf_path.write_bytes(payload)
        out["paths"]["pdf"] = str(pdf_path.relative_to(ROOT))
        out["sizes"]["pdf"] = len(payload)
        out["pdf_name"] = name
        out["pdf_header"] = payload[:5] == b"%PDF-"
        break
    return out


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    settings = get_settings()
    base = settings.odata_base_url.rstrip("/") + "/"
    auth = (settings.odata_username, settings.odata_password)
    fm = load_attached_file_field_map()
    entity = fm["entity"]
    stream_prop = str((fm.get("fields") or {}).get("storage_stream") or "ФайлХранилище")
    b64_field = str((fm.get("fields") or {}).get("storage_binary") or "ФайлХранилище_Base64Data")

    client = ODataClient(
        settings.odata_base_url,
        username=settings.odata_username,
        password=settings.odata_password,
        timeout_sec=120,
    )

    SAVE_DIR.mkdir(parents=True, exist_ok=True)
    items = list_attachments(base, auth, entity, OWNER_REF)

    eml_bytes, eml_source = load_source_eml(settings)
    if eml_bytes:
        (SAVE_DIR / "source.eml").write_bytes(eml_bytes)

    agent_local = build_agent_local_sources(eml_bytes, SAVE_DIR)

    downloads: list[dict] = []
    for item in items:
        ref = item.get("Ref_Key", "")
        desc = (item.get("Description") or "").strip()
        ext = (item.get("Расширение") or "").strip()
        meta_size = int(item.get("Размер") or 0)
        storage_kind = item.get("ТипХраненияФайла") or ""
        file_path_meta = item.get("ПутьКФайлу") or ""

        full_record = client.get_by_key(entity, ref) or item
        stream_bytes = client.get_entity_stream(entity, ref, stream_prop)
        b64_raw = full_record.get(b64_field) or ""
        b64_bytes = b"" 
        if b64_raw:
            try:
                b64_bytes = base64.b64decode(b64_raw)
            except Exception as exc:
                b64_bytes = b""

        if stream_bytes:
            content = stream_bytes
            chosen_method = "stream"
        elif b64_bytes:
            content = b64_bytes
            chosen_method = "base64"
        else:
            content = b""
            chosen_method = "none"

        fname = safe_filename(desc, ext, ref)
        out_path = SAVE_DIR / fname
        out_path.write_bytes(content)

        entry = {
            "ref_key": ref,
            "description": desc,
            "extension": ext,
            "saved_path": str(out_path.relative_to(ROOT)),
            "meta": {k: full_record.get(k) for k in _META_KEYS},
            "download": {
                "stream_bytes": len(stream_bytes),
                "base64_bytes": len(b64_bytes),
                "chosen_method": chosen_method,
                "saved_bytes": len(content),
            },
            "volume_mode": storage_kind == "ВТомахНаДиске",
            "volume_note": (
                "Файл на томе 1С — OData stream пустой, байты на диске сервера"
                if storage_kind == "ВТомахНаДиске" and len(stream_bytes) == 0 and meta_size > 0
                else None
            ),
            "verification": verify_content(content, ext, meta_size),
            "odata_record": slim_record(full_record),
        }
        downloads.append(entry)

    conclusion_parts: list[str] = []
    for d in downloads:
        v = d["verification"]
        meta = int(d["meta"].get("Размер") or 0)
        saved = v["size_bytes"]
        if saved == 0 and meta and meta > 0:
            if d["volume_mode"]:
                conclusion_parts.append(
                    f"{d['description']}.{d['extension']}: metadata {meta} B, OData stream 0 — volume storage at {d['meta'].get('ПутьКФайлу')}"
                )
            else:
                conclusion_parts.append(
                    f"{d['description']}.{d['extension']}: metadata says {meta} B but 0 bytes via OData — CONTENT MISSING IN IB"
                )
        elif saved > 0:
            conclusion_parts.append(
                f"{d['description']}.{d['extension']}: {saved} B downloaded via {d['download']['chosen_method']}"
            )

    report = {
        "document": DOC_NUMBER,
        "owner_ref": OWNER_REF,
        "attachments_count": len(items),
        "source_eml": {
            "source": eml_source,
            "bytes": len(eml_bytes),
            "saved_path": "data/temp/download_762/source.eml" if eml_bytes else None,
        },
        "agent_local_sources": agent_local,
        "downloads": downloads,
        "conclusion": conclusion_parts,
    }
    meta_path = SAVE_DIR / "metadata.json"
    meta_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
