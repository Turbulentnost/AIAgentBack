"""Delete old attachments and re-upload АЛ00-000762.msg with fixed NFC/MIME conversion."""
from __future__ import annotations

import hashlib
import json
import sys
import unicodedata
from pathlib import Path
from urllib.parse import quote

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_pochta.config import get_settings  # noqa: E402
from agent_pochta.services.email_msg import eml_bytes_to_msg_bytes  # noqa: E402
from agent_pochta.services.odata_attached_file import (  # noqa: E402
    AttachedFileInput,
    attach_file_to_incoming_document,
    delete_attached_files_for_document,
    load_attached_file_field_map,
    now_attached_file_processed_at,
    read_attached_file_storage_bytes,
)
from agent_pochta.services.odata_client import ODataClient  # noqa: E402
from agent_pochta.services.odata_integration import resolve_attached_file_author_key  # noqa: E402

DOC_NUMBER = "АЛ00-000762"
DOC_ENTITY = "Document_ТД_ВходящаяКорреспонденция"
EML_FALLBACK = ROOT / "data/temp/compare_762/АЛ00-000762_2026-07-24T07-17-23_АЛ00-000762.eml"


def newest_doc_ref(base: str, auth, number: str) -> str | None:
    url = (
        f"{base}{quote(DOC_ENTITY)}?$format=json"
        f"&$filter={quote(f'Number eq {chr(39)}{number}{chr(39)}')}"
        f"&$orderby=Date desc&$top=1"
    )
    items = httpx.get(url, auth=auth, timeout=120).json().get("value", [])
    return items[0].get("Ref_Key") if items else None


def ole_attachment_meta(content: bytes, out_path: Path) -> dict:
    import olefile

    out_path.write_bytes(content)
    ole = olefile.OleFileIO(str(out_path))
    if not ole.exists(("__attach_version1.0_#00000000", "__substg1.0_3707001F")):
        ole.close()
        return {"attachments": 0}
    name = ole.openstream(
        ("__attach_version1.0_#00000000", "__substg1.0_3707001F")
    ).read()
    mime = ole.openstream(
        ("__attach_version1.0_#00000000", "__substg1.0_370E001F")
    ).read()
    ole.close()
    name_txt = name.decode("utf-16-le").split("\x00")[0]
    mime_txt = mime.decode("utf-16-le").split("\x00")[0]
    return {
        "attachment_name": name_txt,
        "has_combining": any(unicodedata.combining(c) for c in name_txt),
        "attachment_mime": mime_txt,
        "sha256": hashlib.sha256(content).hexdigest(),
        "size": len(content),
        "cfb": content[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",
    }


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
    doc_ref = newest_doc_ref(base, auth, DOC_NUMBER)
    if not doc_ref:
        print(json.dumps({"error": f"document {DOC_NUMBER} not found"}, ensure_ascii=False))
        raise SystemExit(1)

    if not EML_FALLBACK.is_file():
        print(json.dumps({"error": f"eml not found: {EML_FALLBACK}"}, ensure_ascii=False))
        raise SystemExit(1)

    deleted = delete_attached_files_for_document(
        client, document_ref_key=doc_ref, field_map=fm
    )
    eml_bytes = EML_FALLBACK.read_bytes()
    msg_bytes = eml_bytes_to_msg_bytes(eml_bytes)
    out_dir = ROOT / "data/temp/compare_762"
    out_dir.mkdir(parents=True, exist_ok=True)
    local_meta = ole_attachment_meta(msg_bytes, out_dir / "_reattach762.msg")

    author = resolve_attached_file_author_key(
        explicit_key=settings.odata_file_author_key or "",
        incoming_defaults_file=settings.odata_incoming_defaults_file,
    )
    result = attach_file_to_incoming_document(
        client,
        document_ref_key=doc_ref,
        file_input=AttachedFileInput(
            filename=f"{DOC_NUMBER}.msg",
            content=msg_bytes,
            author_key=author or None,
            processed_at=now_attached_file_processed_at(),
        ),
        field_map=fm,
    )
    stored = read_attached_file_storage_bytes(
        client,
        entity=result.entity,
        ref_key=result.ref_key,
        field_map=fm,
    )
    report = {
        "document": DOC_NUMBER,
        "doc_ref": doc_ref,
        "deleted_refs": deleted,
        "new_ref_key": result.ref_key,
        "local_msg": local_meta,
        "stored_bytes": len(stored),
        "stored_eq_local": stored == msg_bytes,
        "odata_meta": {
            k: (client.get_by_key(result.entity, result.ref_key) or {}).get(k)
            for k in (
                "ТипХраненияФайла",
                "ФайлХранилище_Type",
                "Размер",
                "Редактирует_Key",
                "DeletionMark",
                "ДатаСоздания",
            )
        },
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
