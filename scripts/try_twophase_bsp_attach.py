"""Try two-phase attach: POST meta → PATCH Base64 to trigger 1C ПередЗаписью/ПриЗаписи."""
from __future__ import annotations

import base64
import json
import os
import sys
from pathlib import Path

os.environ.setdefault("ODATA_ATTACH_STAGING_ENABLED", "false")
os.environ.setdefault("ODATA_FILE_STORAGE_MODE", "database")

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
    delete_attached_file_refs,
    list_attached_files_for_document,
    load_attached_file_field_map,
    now_attached_file_processed_at,
    format_attached_file_created_at,
    format_attached_file_modified_universal,
    release_attached_file_edit_lock,
    split_filename,
)
from agent_pochta.services.odata_client import ODataClient  # noqa: E402
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
    doc_ref = row.erp_task_id
    email = EmailMessage(
        message_id=row.message_id,
        mailbox=row.mailbox or "",
        sender_email=row.sender_email or "",
        subject=row.subject or "",
        received_at=row.received_at,
        attachments=[],
    )
    eml = ensure_full_email_bytes_for_erp(email, StubVaultClient())
    msg_name = erp_full_email_filename(email, erp_document_number=DOC)
    msg = eml_bytes_to_msg_bytes(eml, embed_attachments=True)
    base_name, ext = split_filename(msg_name)
    ts = now_attached_file_processed_at()

    settings = get_settings()
    fm = load_attached_file_field_map()
    entity = fm["entity"]
    client = ODataClient(
        settings.odata_base_url,
        username=settings.odata_username,
        password=settings.odata_password,
        timeout_sec=120,
    )
    existing = list_attached_files_for_document(
        client, document_ref_key=doc_ref, field_map=fm
    )
    old = [
        str(i.get("Ref_Key"))
        for i in existing
        if str(i.get("Description") or "") == DOC and i.get("Ref_Key")
    ]

    # Phase 1: POST without binary
    post = {
        "Description": base_name,
        "Расширение": ext,
        "ВладелецФайла_Key": doc_ref,
        "Размер": len(msg),
        "ДатаСоздания": format_attached_file_created_at(ts),
        "ДатаМодификацииУниверсальная": format_attached_file_modified_universal(ts),
    }
    created = client.create_entity(entity, post)
    ref = created["Ref_Key"]
    print("phase1_ref", ref)

    # Phase 2: PATCH Base64 to trigger BeforeWrite with field filled
    patch = {
        "ФайлХранилище_Base64Data": base64.b64encode(msg).decode("ascii"),
        "ФайлХранилище_Type": "application/octet-stream",
        "Размер": len(msg),
    }
    client.patch_entity(entity, ref, patch)
    release_attached_file_edit_lock(client, entity=entity, ref_key=ref, field_map=fm)

    meta = client.get_by_key(entity, ref) or {}
    b64 = meta.get("ФайлХранилище_Base64Data") or ""
    report = {
        "ref": ref,
        "ТипХраненияФайла": meta.get("ТипХраненияФайла"),
        "Размер": meta.get("Размер"),
        "ПутьКФайлу": meta.get("ПутьКФайлу"),
        "Том_Key": meta.get("Том_Key"),
        "Изменил_Key": meta.get("Изменил_Key"),
        "b64_len": len(b64),
        "exchange_cleared": len(b64) == 0,
        "bsp_ok": bool(str(meta.get("ТипХраненияФайла") or "").strip()) and len(b64) == 0,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))

    # cleanup old agent files only
    delete_attached_file_refs(
        client, ref_keys=[r for r in old if r != ref], field_map=fm
    )


if __name__ == "__main__":
    main()
