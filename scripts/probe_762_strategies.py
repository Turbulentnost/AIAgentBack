"""Upload strategy probes for АЛ00-000762 — isolate 1C thick-client open failure."""
from __future__ import annotations

import json
import os
import sys
import tempfile
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_pochta.config import get_settings  # noqa: E402
from agent_pochta.services.email_msg import (  # noqa: E402
    eml_bytes_to_msg_bytes,
    normalize_attachment_filename,
)
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

DOC762 = "18516943-871f-11f1-984b-6cb31113810e"
DOC_NUMBER = "АЛ00-000762"
REF_760 = "27997dc5-8689-11f1-984a-6cb31113810e"
ENTITY = "Catalog_ТД_ВходящаяКорреспонденцияПрисоединенныеФайлы"


def find_762_eml() -> Path:
    for path in sorted((ROOT / "data/temp/compare_762").glob("*000762.eml")):
        if "PROBE" not in path.name:
            return path
    raise FileNotFoundError("762 EML not found under data/temp/compare_762")


def eml_body_only(eml_bytes: bytes) -> bytes:
    """RFC822 без вложений — только заголовки и тело."""
    msg = BytesParser(policy=policy.default).parsebytes(eml_bytes)
    out = EmailMessage()
    for header in ("From", "To", "Cc", "Subject", "Date", "Message-ID", "Reply-To"):
        value = msg.get(header)
        if value:
            out[header] = value
    body = msg.get_body(preferencelist=("plain", "html"))
    if body is None:
        out.set_content("")
        return out.as_bytes()
    content = body.get_content()
    subtype = body.get_content_subtype()
    charset = body.get_content_charset() or "utf-8"
    if subtype == "html":
        out.set_content(content, subtype="html", charset=charset)
    else:
        out.set_content(content, charset=charset)
    return out.as_bytes()


def eml_to_msg_fixed_mime(eml_bytes: bytes) -> bytes:
    """Конвертация с явной установкой filename/mime_type через API Aspose FOSS."""
    from agent_pochta.services.email_msg import (
        _guess_mime_from_filename,
        _normalize_eml_attachment_headers,
    )
    from aspose.email_foss import msg as msgmod

    email_message = BytesParser(policy=policy.default).parsebytes(eml_bytes)
    _normalize_eml_attachment_headers(email_message)
    message = msgmod.MapiMessage.from_email_message(email_message)
    for att in message.attachments or []:
        raw_name = getattr(att, "filename", None)
        name = normalize_attachment_filename(raw_name)
        if name and hasattr(att, "filename"):
            att.filename = name
        mime = _guess_mime_from_filename(name or "")
        if mime and hasattr(att, "mime_type"):
            att.mime_type = mime
    with tempfile.NamedTemporaryFile(suffix=".msg", delete=False) as tmp:
        path = tmp.name
    try:
        message.save(path)
        return Path(path).read_bytes()
    finally:
        os.unlink(path)


def count_attachments(msg_bytes: bytes) -> int:
    import olefile

    with tempfile.NamedTemporaryFile(suffix=".msg", delete=False) as tmp:
        path = tmp.name
    try:
        Path(path).write_bytes(msg_bytes)
        ole = olefile.OleFileIO(path)
        index = 0
        while ole.exists(("__attach_version1.0_#%08X" % index, "__properties_version1.0")):
            index += 1
        ole.close()
        return index
    finally:
        Path(path).unlink(missing_ok=True)


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    strategy = (sys.argv[1] if len(sys.argv) > 1 else "no_attach").strip().lower()
    allowed = {"no_attach", "760_copy", "fixed_mime", "full"}
    if strategy not in allowed:
        print(json.dumps({"error": f"strategy must be one of {sorted(allowed)}"}, ensure_ascii=False))
        raise SystemExit(2)

    settings = get_settings()
    fm = load_attached_file_field_map()
    client = ODataClient(
        settings.odata_base_url,
        username=settings.odata_username,
        password=settings.odata_password,
        timeout_sec=120,
    )
    author = resolve_attached_file_author_key(
        explicit_key=settings.odata_file_author_key or "",
        incoming_defaults_file=settings.odata_incoming_defaults_file,
    )
    eml_bytes = find_762_eml().read_bytes()

    if strategy == "760_copy":
        msg_bytes = read_attached_file_storage_bytes(
            client, entity=ENTITY, ref_key=REF_760, field_map=fm
        )
        label = "760-bytes-on-762"
    elif strategy == "no_attach":
        msg_bytes = eml_bytes_to_msg_bytes(eml_body_only(eml_bytes))
        label = "body-only-no-embedded-attachments"
    elif strategy == "fixed_mime":
        msg_bytes = eml_to_msg_fixed_mime(eml_bytes)
        label = "fixed-filename-mime-api"
    else:
        msg_bytes = eml_bytes_to_msg_bytes(eml_bytes)
        label = "full-with-embedded-pdf"

    deleted = delete_attached_files_for_document(client, document_ref_key=DOC762, field_map=fm)
    result = attach_file_to_incoming_document(
        client,
        document_ref_key=DOC762,
        file_input=AttachedFileInput(
            filename=f"{DOC_NUMBER}.msg",
            content=msg_bytes,
            author_key=author or None,
            processed_at=now_attached_file_processed_at(),
        ),
        field_map=fm,
    )
    stored = read_attached_file_storage_bytes(
        client, entity=ENTITY, ref_key=result.ref_key, field_map=fm
    )
    report = {
        "strategy": strategy,
        "label": label,
        "deleted_refs": deleted,
        "new_ref_key": result.ref_key,
        "local_size": len(msg_bytes),
        "stored_size": len(stored),
        "stored_eq_local": stored == msg_bytes,
        "attachment_count_in_msg": count_attachments(msg_bytes),
        "verify_in_1c": f"Open {DOC_NUMBER} dated 24.07.2026, attachment Ref_Key={result.ref_key}",
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
