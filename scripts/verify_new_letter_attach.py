"""End-to-end verify: DB email → EML → MSG → OData upload (staging) → round-trip bytes."""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

# Keep staged files + roundtrip report for inspection
os.environ.setdefault("ODATA_ATTACH_STAGING_ENABLED", "true")
os.environ.setdefault("ODATA_ATTACH_STAGING_DELETE_AFTER_SUCCESS", "false")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_pochta.config import get_settings  # noqa: E402
from agent_pochta.db.models import EmailMessageRow  # noqa: E402
from agent_pochta.db.session import get_session_factory  # noqa: E402
from agent_pochta.schemas import EmailMessage  # noqa: E402
from agent_pochta.services.email_msg import eml_bytes_to_msg_bytes, is_msg_bytes  # noqa: E402
from agent_pochta.services.erp_attachments import (  # noqa: E402
    ensure_full_email_bytes_for_erp,
    erp_full_email_filename,
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
from agent_pochta.services.vault import StubVaultClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

OUT_DIR = ROOT / "data" / "temp" / "verify_new_letter"
_OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_EMPTY_GUID = "00000000-0000-0000-0000-000000000000"
META_KEYS = (
    "ТипХраненияФайла",
    "Том_Key",
    "ПутьКФайлу",
    "ФайлХранилище_Type",
    "Размер",
    "Редактирует_Key",
    "Изменил_Key",
    "Автор_Key",
    "DeletionMark",
    "ДатаСоздания",
    "Description",
    "Расширение",
)


def msg_embedded_count(content: bytes) -> int | None:
    if not is_msg_bytes(content):
        return None
    try:
        import olefile
    except ImportError:
        return None

    with tempfile.NamedTemporaryFile(suffix=".msg", delete=False) as tmp:
        path = tmp.name
    try:
        Path(path).write_bytes(content)
        ole = olefile.OleFileIO(path)
        index = 0
        while ole.exists(("__attach_version1.0_#%08X" % index, "__properties_version1.0")):
            index += 1
        ole.close()
        return index
    finally:
        Path(path).unlink(missing_ok=True)


def load_email_row(doc_number: str) -> EmailMessageRow:
    factory = get_session_factory()
    with factory() as session:
        row = session.scalar(
            select(EmailMessageRow)
            .where(EmailMessageRow.erp_document_number == doc_number)
            .order_by(EmailMessageRow.id.desc())
        )
        if row is None:
            raise SystemExit(f"No DB row for erp_document_number={doc_number!r}")
        session.expunge(row)
        return row


def row_to_email(row: EmailMessageRow) -> EmailMessage:
    return EmailMessage(
        message_id=row.message_id,
        mailbox=row.mailbox or "",
        sender_email=row.sender_email or "",
        subject=row.subject or "",
        received_at=row.received_at,
        attachments=[],
    )


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="Verify ERP attachment round-trip for a document")
    parser.add_argument("--doc", default="НП00-003900", help="ERP document number")
    args = parser.parse_args()
    doc_number = args.doc.strip()

    row = load_email_row(doc_number)
    doc_ref = (row.erp_task_id or "").strip()
    if not doc_ref or doc_ref in {"SKIP-ERP", "DRY-RUN"}:
        raise SystemExit(f"Invalid erp_task_id for {doc_number}: {doc_ref!r}")

    email = row_to_email(row)
    vault = StubVaultClient()
    eml_bytes = ensure_full_email_bytes_for_erp(email, vault)
    msg_filename = erp_full_email_filename(email, erp_document_number=doc_number)
    msg_bytes = eml_bytes_to_msg_bytes(eml_bytes, embed_attachments=True)

    settings = get_settings()
    fm = load_attached_file_field_map()
    entity = fm["entity"]
    client = ODataClient(
        settings.odata_base_url,
        username=settings.odata_username,
        password=settings.odata_password,
        timeout_sec=max(settings.odata_timeout_sec, 120),
    )
    author = resolve_attached_file_author_key(
        explicit_key=settings.odata_file_author_key or "",
        incoming_defaults_file=settings.odata_incoming_defaults_file,
    )

    deleted_refs = delete_attached_files_for_document(
        client, document_ref_key=doc_ref, field_map=fm
    )

    result = attach_file_to_incoming_document(
        client,
        document_ref_key=doc_ref,
        file_input=AttachedFileInput(
            filename=msg_filename,
            content=msg_bytes,
            author_key=author or None,
            processed_at=now_attached_file_processed_at(),
        ),
        field_map=fm,
        verify_owner_exists=True,
        document_number=doc_number,
        message_id=email.message_id,
    )

    odata_bytes = read_attached_file_storage_bytes(
        client, entity=entity, ref_key=result.ref_key, field_map=fm
    )
    meta = client.get_by_key(entity, result.ref_key) or {}

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    local_path = OUT_DIR / msg_filename
    odata_path = OUT_DIR / f"odata_{msg_filename}"
    local_path.write_bytes(msg_bytes)
    if odata_bytes:
        odata_path.write_bytes(odata_bytes)

    embedded = msg_embedded_count(msg_bytes)
    bytes_match = bool(odata_bytes) and odata_bytes == msg_bytes
    editor_key = str(meta.get("Редактирует_Key") or _EMPTY_GUID).strip()

    report = {
        "verified_at_utc": datetime.now(timezone.utc).isoformat(),
        "document_number": doc_number,
        "document_ref_key": doc_ref,
        "message_id": email.message_id,
        "subject": email.subject,
        "status": row.status,
        "deleted_old_refs": deleted_refs,
        "msg_filename": msg_filename,
        "msg_ref_key": result.ref_key,
        "msg_size_local": len(msg_bytes),
        "msg_size_odata": len(odata_bytes),
        "bytes_match": bytes_match,
        "roundtrip_ok": result.roundtrip_ok,
        "staging_path": result.staging_path,
        "msg_embedded_attachments": embedded,
        "msg_opens_as_ole": msg_bytes[:8] == _OLE_MAGIC,
        "odata_meta": {k: meta.get(k) for k in META_KEYS},
        "storage_kind": meta.get("ТипХраненияФайла"),
        "editor_key_empty": editor_key == _EMPTY_GUID,
        "local_path": str(local_path),
        "odata_path": str(odata_path) if odata_bytes else None,
        "pass": (
            bytes_match
            and len(msg_bytes) > 1000
            and meta.get("ТипХраненияФайла") == "ВИнформационнойБазе"
            and meta.get("DeletionMark") is False
            and editor_key == _EMPTY_GUID
        ),
    }

    report_path = OUT_DIR / f"{doc_number}_verify_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
