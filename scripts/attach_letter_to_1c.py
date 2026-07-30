"""Attach letter MSG to a 1C document (default: volume + stream PUT via 1C server)."""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

os.environ.setdefault("ODATA_ATTACH_STAGING_ENABLED", "false")
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

DOC_NUMBER = sys.argv[1] if len(sys.argv) > 1 else "НП00-003921"
EMPTY = "00000000-0000-0000-0000-000000000000"
META = (
    "ТипХраненияФайла",
    "Том_Key",
    "ПутьКФайлу",
    "ФайлХранилище_Type",
    "Размер",
    "Редактирует_Key",
    "Изменил_Key",
    "Автор_Key",
    "ДатаСоздания",
    "ДатаМодификацииУниверсальная",
    "Description",
    "Расширение",
)


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    factory = get_session_factory()
    with factory() as session:
        row = session.scalar(
            select(EmailMessageRow)
            .where(EmailMessageRow.erp_document_number == DOC_NUMBER)
            .order_by(EmailMessageRow.id.desc())
        )
        if row is None:
            raise SystemExit(f"No DB row for {DOC_NUMBER}")
        session.expunge(row)

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
    msg_name = erp_full_email_filename(email, erp_document_number=DOC_NUMBER)
    msg = eml_bytes_to_msg_bytes(eml, embed_attachments=True)

    settings = get_settings()
    fm = load_attached_file_field_map()
    entity = fm["entity"]
    client = ODataClient(
        settings.odata_base_url,
        username=settings.odata_username,
        password=settings.odata_password,
        timeout_sec=max(settings.odata_timeout_sec, 120),
    )
    existing = list_attached_files_for_document(
        client, document_ref_key=doc_ref, field_map=fm
    )
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
        document_number=DOC_NUMBER,
        message_id=email.message_id,
    )
    refs_to_delete = [
        str(i.get("Ref_Key") or "").strip()
        for i in existing
        if str(i.get("Description") or "").strip() == DOC_NUMBER
        and str(i.get("Ref_Key") or "").strip()
        and str(i.get("Ref_Key") or "").strip() != result.ref_key
    ]
    deleted = delete_attached_file_refs(client, ref_keys=refs_to_delete, field_map=fm)
    odata = read_attached_file_storage_bytes(
        client, entity=entity, ref_key=result.ref_key, field_map=fm
    )
    meta = client.get_by_key(entity, result.ref_key) or {}
    report = {
        "at_utc": datetime.now(timezone.utc).isoformat(),
        "doc": DOC_NUMBER,
        "doc_ref": doc_ref,
        "subject": email.subject,
        "new_ref": result.ref_key,
        "deleted_agent_refs": deleted,
        "post_keys": sorted(post_payload.keys()),
        "storage_mode": (fm.get("defaults") or {}).get("storage_mode"),
        "msg_size": len(msg),
        "odata_stream_len": len(odata),
        "meta": {k: meta.get(k) for k in META},
        "editor_empty": str(meta.get("Редактирует_Key") or EMPTY) == EMPTY,
        "looks_like_manual_volume": (
            str(meta.get("ТипХраненияФайла") or "") == "ВТомахНаДиске"
            and bool(str(meta.get("ПутьКФайлу") or "").strip())
            and str(meta.get("Размер") or "") == str(len(msg))
        ),
    }
    out = ROOT / "data" / "temp" / f"attach_{DOC_NUMBER}_report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
