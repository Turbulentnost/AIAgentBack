"""Reattach full .msg to НП00-003921 in database/Base64 mode; check BSP extension."""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

os.environ.setdefault("ODATA_ATTACH_STAGING_ENABLED", "false")
os.environ.setdefault("ODATA_FILE_STORAGE_MODE", "database")
os.environ.setdefault("ODATA_FILE_VOLUME_PREUPLOAD", "false")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sqlalchemy import select  # noqa: E402

from agent_pochta.config import get_settings  # noqa: E402
from agent_pochta.db.models import EmailMessageRow  # noqa: E402
from agent_pochta.db.session import get_session_factory  # noqa: E402
from agent_pochta.schemas import EmailMessage  # noqa: E402
from agent_pochta.services.email_msg import eml_bytes_to_msg_bytes  # noqa: E402
from agent_pochta.services.erp_attachments import (  # noqa: E402
    ensure_full_email_bytes_for_erp,
    erp_full_email_filename,
)
from agent_pochta.services.odata_attached_file import (  # noqa: E402
    AttachedFileInput,
    attach_file_to_incoming_document,
    build_attached_file_payload,
    delete_attached_file_refs,
    list_attached_files_for_document,
    load_attached_file_field_map,
    now_attached_file_processed_at,
    read_attached_file_storage_bytes,
)
from agent_pochta.services.odata_client import ODataClient  # noqa: E402
from agent_pochta.services.vault import StubVaultClient  # noqa: E402

DOC = sys.argv[1] if len(sys.argv) > 1 else "НП00-003921"
EMPTY = "00000000-0000-0000-0000-000000000000"
META_KEYS = (
    "ТипХраненияФайла",
    "Том_Key",
    "ПутьКФайлу",
    "ФайлХранилище_Type",
    "ФайлХранилище_Base64Data",
    "Размер",
    "Редактирует_Key",
    "Изменил_Key",
    "Автор_Key",
    "ДатаСоздания",
    "ДатаМодификацииУниверсальная",
    "Description",
    "Расширение",
    "DeletionMark",
)


def _b64_len(meta: dict) -> int:
    raw = meta.get("ФайлХранилище_Base64Data")
    if raw is None:
        return 0
    if isinstance(raw, str):
        return len(raw.strip())
    return len(str(raw))


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    factory = get_session_factory()
    with factory() as session:
        row = session.scalar(
            select(EmailMessageRow)
            .where(EmailMessageRow.erp_document_number == DOC)
            .order_by(EmailMessageRow.id.desc())
        )
        if row is None:
            # fallback: latest incoming with erp doc
            row = session.scalar(
                select(EmailMessageRow)
                .where(EmailMessageRow.erp_document_number.is_not(None))
                .where(EmailMessageRow.erp_document_number != "")
                .order_by(EmailMessageRow.id.desc())
            )
            if row is None:
                raise SystemExit(f"No DB row for {DOC} and no fallback")
            print(f"WARN: {DOC} not found, using {row.erp_document_number}", flush=True)
        session.expunge(row)

    doc_number = (row.erp_document_number or DOC).strip()
    doc_ref = (row.erp_task_id or "").strip()
    email = EmailMessage(
        message_id=row.message_id,
        mailbox=row.mailbox or "",
        sender_email=row.sender_email or "",
        subject=row.subject or "",
        received_at=row.received_at,
        attachments=[],
    )
    eml = ensure_full_email_bytes_for_erp(email, StubVaultClient())
    msg_name = erp_full_email_filename(email, erp_document_number=doc_number)
    msg = eml_bytes_to_msg_bytes(eml, embed_attachments=True)

    settings = get_settings()
    fm = load_attached_file_field_map()
    defaults = dict(fm.get("defaults") or {})
    defaults.update(
        {
            "storage_mode": "database",
            "storage_kind": "ВИнформационнойБазе",
            "upload_binary_via_stream": False,
            "volume_preupload": False,
            "omit_storage_kind": True,
            "verify_mode": "bsp_exchange",
            "minimal_payload": True,
        }
    )
    fm = {**fm, "defaults": defaults}
    entity = fm["entity"]
    client = ODataClient(
        settings.odata_base_url,
        username=settings.odata_username,
        password=settings.odata_password,
        timeout_sec=max(settings.odata_timeout_sec, 180),
    )

    existing = list_attached_files_for_document(
        client, document_ref_key=doc_ref, field_map=fm
    )
    old_refs = [
        str(i.get("Ref_Key") or "").strip()
        for i in existing
        if str(i.get("Description") or "").strip() == doc_number
        and str(i.get("Ref_Key") or "").strip()
    ]
    deleted_before = delete_attached_file_refs(client, ref_keys=old_refs, field_map=fm)

    file_input = AttachedFileInput(
        filename=msg_name,
        content=msg,
        processed_at=now_attached_file_processed_at(),
    )
    _entity, post_payload = build_attached_file_payload(
        document_ref_key=doc_ref,
        file_input=file_input,
        field_map=fm,
    )
    result = attach_file_to_incoming_document(
        client,
        document_ref_key=doc_ref,
        file_input=file_input,
        field_map=fm,
        verify_owner_exists=True,
        document_number=doc_number,
        message_id=email.message_id,
    )

    meta = client.get_by_key(entity, result.ref_key) or {}
    stream = read_attached_file_storage_bytes(
        client, entity=entity, ref_key=result.ref_key, field_map=fm
    )
    b64_len = _b64_len(meta)
    kind = str(meta.get("ТипХраненияФайла") or "").strip()
    path = str(meta.get("ПутьКФайлу") or "").strip()
    size = str(meta.get("Размер") or "")
    editor = str(meta.get("Редактирует_Key") or EMPTY)
    # Success: Base64 exchange cleared AND storage kind finalized by BSP.
    bsp_ran = b64_len == 0 and bool(kind) and size == str(len(msg))
    report = {
        "at_utc": datetime.now(timezone.utc).isoformat(),
        "doc": doc_number,
        "doc_ref": doc_ref,
        "subject": email.subject,
        "new_ref": result.ref_key,
        "deleted_before": deleted_before,
        "storage_mode": defaults.get("storage_mode"),
        "verify_mode": defaults.get("verify_mode"),
        "omit_storage_kind": defaults.get("omit_storage_kind"),
        "post_keys": sorted(post_payload.keys()),
        "post_has_b64": "ФайлХранилище_Base64Data" in post_payload,
        "msg_size": len(msg),
        "b64_len": b64_len,
        "stream_len": len(stream),
        "kind": kind,
        "path": path,
        "size": size,
        "editor_empty": editor == EMPTY,
        "bsp_extension_likely_ran": bsp_ran,
        "meta": {k: meta.get(k) for k in META_KEYS},
    }
    # Don't dump huge base64 into report file
    if report["meta"].get("ФайлХранилище_Base64Data"):
        report["meta"]["ФайлХранилище_Base64Data"] = f"<len={b64_len}>"

    out = ROOT / "data" / "temp" / f"attach_{doc_number}_ib_bsp_report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
