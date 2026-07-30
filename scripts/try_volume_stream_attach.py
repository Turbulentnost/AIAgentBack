"""Attach via volume metadata + OData stream PUT (1C server writes to tom)."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

os.environ.setdefault("ODATA_ATTACH_STAGING_ENABLED", "false")
os.environ["ODATA_FILE_STORAGE_MODE"] = "volume"
os.environ["ODATA_FILE_VOLUME_PREUPLOAD"] = "false"

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
    delete_attached_file_refs,
    list_attached_files_for_document,
    now_attached_file_processed_at,
    read_attached_file_storage_bytes,
)
from agent_pochta.services.odata_client import ODataClient  # noqa: E402
from agent_pochta.services.odata_integration import ODataIntegrationService  # noqa: E402
from agent_pochta.services.vault import StubVaultClient  # noqa: E402

DOC = "НП00-003921"


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
        if not row:
            raise SystemExit("no row")
        session.expunge(row)

    email = EmailMessage(
        message_id=row.message_id,
        mailbox=row.mailbox or "",
        sender_email=row.sender_email or "",
        subject=row.subject or "",
        received_at=row.received_at,
        attachments=[],
    )
    msg = eml_bytes_to_msg_bytes(
        ensure_full_email_bytes_for_erp(email, StubVaultClient()),
        embed_attachments=True,
    )
    msg_name = erp_full_email_filename(email, erp_document_number=DOC)
    settings = get_settings()
    svc = ODataIntegrationService(
        settings.odata_base_url,
        entity=settings.odata_incoming_doc_entity,
        username=settings.odata_username,
        password=settings.odata_password,
        timeout_sec=120,
        file_volume_key=settings.odata_file_volume_key,
        file_author_key="",
        file_storage_mode="volume",
        file_volume_preupload=False,
    )
    fm = dict(svc._attached_file_field_map)
    defaults = dict(fm.get("defaults") or {})
    defaults.update(
        {
            "storage_mode": "volume",
            "storage_kind": "ВТомахНаДиске",
            "volume_preupload": False,
            "omit_storage_kind": False,
            "minimal_payload": True,
            "verify_mode": "volume",
            "upload_binary_via_stream": True,
        }
    )
    fm["defaults"] = defaults

    client = ODataClient(
        settings.odata_base_url,
        username=settings.odata_username,
        password=settings.odata_password,
        timeout_sec=120,
    )
    doc_ref = row.erp_task_id
    existing = list_attached_files_for_document(
        client, document_ref_key=doc_ref, field_map=fm
    )
    old = [
        str(i.get("Ref_Key"))
        for i in existing
        if str(i.get("Description") or "") == DOC and i.get("Ref_Key")
    ]

    result = attach_file_to_incoming_document(
        client,
        document_ref_key=doc_ref,
        file_input=AttachedFileInput(
            filename=msg_name,
            content=msg,
            processed_at=now_attached_file_processed_at(),
        ),
        field_map=fm,
        document_number=DOC,
        message_id=email.message_id,
    )
    delete_attached_file_refs(
        client, ref_keys=[r for r in old if r != result.ref_key], field_map=fm
    )
    meta = client.get_by_key(fm["entity"], result.ref_key) or {}
    odata = read_attached_file_storage_bytes(
        client, entity=fm["entity"], ref_key=result.ref_key, field_map=fm
    )
    report = {
        "ref": result.ref_key,
        "ТипХраненияФайла": meta.get("ТипХраненияФайла"),
        "ПутьКФайлу": meta.get("ПутьКФайлу"),
        "Том_Key": meta.get("Том_Key"),
        "Размер": meta.get("Размер"),
        "odata_stream_len": len(odata),
        "Изменил_Key": meta.get("Изменил_Key"),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
