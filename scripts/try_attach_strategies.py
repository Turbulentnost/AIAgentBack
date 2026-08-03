"""Probe BSP binary registers and try multiple attach strategies on one doc."""
from __future__ import annotations

import base64
import json
import os
import re
import sys
from pathlib import Path

os.environ.setdefault("ODATA_ATTACH_STAGING_ENABLED", "false")
os.environ.setdefault("ODATA_FILE_VOLUME_PREUPLOAD", "false")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import httpx
from sqlalchemy import select

from agent_pochta.config import get_settings
from agent_pochta.db.models import EmailMessageRow
from agent_pochta.db.session import get_session_factory
from agent_pochta.schemas import EmailMessage
from agent_pochta.services.email_msg import eml_bytes_to_msg_bytes
from agent_pochta.services.erp_attachments import (
    ensure_full_email_bytes_for_erp,
    erp_full_email_filename,
)
from agent_pochta.services.odata_attached_file import (
    AttachedFileInput,
    attach_file_to_incoming_document,
    delete_attached_file_refs,
    format_attached_file_created_at,
    format_attached_file_modified_universal,
    format_volume_file_path,
    list_attached_files_for_document,
    load_attached_file_field_map,
    now_attached_file_processed_at,
    read_attached_file_storage_bytes,
    release_attached_file_edit_lock,
    split_filename,
    _DEFAULT_VOLUME_KEY,
)
from agent_pochta.services.odata_client import ODataClient
from agent_pochta.services.odata_integration import resolve_attached_file_author_key
from agent_pochta.services.vault import StubVaultClient

DOC = sys.argv[1] if len(sys.argv) > 1 else "НП00-003921"
ENTITY = "Catalog_ТД_ВходящаяКорреспонденцияПрисоединенныеФайлы"
EMPTY = "00000000-0000-0000-0000-000000000000"
VOL = "21886495-364e-11ea-82f2-ac1f6b05524c"


def score_meta(meta: dict, content: bytes, odata: bytes) -> dict:
    kind = str(meta.get("ТипХраненияФайла") or "").strip()
    path = str(meta.get("ПутьКФайлу") or "").strip()
    size = str(meta.get("Размер") or "")
    b64 = meta.get("ФайлХранилище_Base64Data") or ""
    return {
        "kind": kind,
        "path": path,
        "size": size,
        "size_ok": size == str(len(content)),
        "b64_len": len(b64),
        "stream_len": len(odata),
        "exchange_cleared": len(b64) == 0 and len(odata) == 0,
        "has_volume_path": kind == "ВТомахНаДиске" and bool(path),
        "has_ib_kind": kind == "ВИнформационнойБазе",
        "has_bytes_somewhere": len(b64) > 0 or len(odata) > 0 or (
            kind == "ВТомахНаДиске" and size == str(len(content))
        ),
        "Изменил_Key": meta.get("Изменил_Key"),
        "Автор_Key": meta.get("Автор_Key"),
    }


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    settings = get_settings()
    auth = (settings.odata_username, settings.odata_password)
    base = settings.odata_base_url.rstrip("/") + "/"

    # --- metadata: binary registers ---
    meta_xml = httpx.get(base + "$metadata", auth=auth, timeout=120).text
    et = re.findall(r'EntityType Name="([^"]+)"', meta_xml)
    interesting = [
        n
        for n in et
        if any(
            k in n
            for k in (
                "Двоич",
                "ХранилищеФайлов",
                "СведенияОФайлах",
                "ВерсииФайлов",
                "ТомаХранения",
                "НаличиеФайлов",
            )
        )
    ]
    print("=== entities ===")
    for n in interesting:
        print(n)

    factory = get_session_factory()
    with factory() as session:
        row = session.scalar(
            select(EmailMessageRow)
            .where(EmailMessageRow.erp_document_number == DOC)
            .order_by(EmailMessageRow.id.desc())
        )
        if not row:
            raise SystemExit(f"no row {DOC}")
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
    msg = eml_bytes_to_msg_bytes(
        ensure_full_email_bytes_for_erp(email, StubVaultClient()),
        embed_attachments=True,
    )
    msg_name = erp_full_email_filename(email, erp_document_number=DOC)
    base_name, ext = split_filename(msg_name)
    ts = now_attached_file_processed_at()
    author = resolve_attached_file_author_key(
        explicit_key=settings.odata_file_author_key or "",
        incoming_defaults_file=settings.odata_incoming_defaults_file,
    )
    client = ODataClient(
        settings.odata_base_url,
        username=settings.odata_username,
        password=settings.odata_password,
        timeout_sec=120,
    )
    fm = load_attached_file_field_map()

    # wipe agent files for this doc name
    existing = list_attached_files_for_document(
        client, document_ref_key=doc_ref, field_map=fm
    )
    old = [
        str(i.get("Ref_Key"))
        for i in existing
        if str(i.get("Description") or "") == DOC and i.get("Ref_Key")
    ]
    if old:
        delete_attached_file_refs(client, ref_keys=old, field_map=fm)

    results = []

    def finish(label: str, ref: str) -> None:
        release_attached_file_edit_lock(
            client, entity=ENTITY, ref_key=ref, field_map=fm, author_key=author or None
        )
        meta = client.get_by_key(ENTITY, ref) or {}
        odata = read_attached_file_storage_bytes(
            client, entity=ENTITY, ref_key=ref, field_map=fm
        )
        item = {"strategy": label, "ref": ref, **score_meta(meta, msg, odata)}
        results.append(item)
        print(json.dumps(item, ensure_ascii=False))

    # A: IB + Base64 + full meta + author/editor
    payload_a = {
        "Description": f"{base_name}_A",
        "Расширение": ext,
        "ВладелецФайла_Key": doc_ref,
        "ТипХраненияФайла": "ВИнформационнойБазе",
        "Размер": len(msg),
        "ДатаСоздания": format_attached_file_created_at(ts),
        "ДатаМодификацииУниверсальная": format_attached_file_modified_universal(ts),
        "ФайлХранилище_Base64Data": base64.b64encode(msg).decode("ascii"),
        "ФайлХранилище_Type": "application/octet-stream",
    }
    if author:
        payload_a["Автор_Key"] = author
        payload_a["Изменил_Key"] = author
    ref_a = client.create_entity(ENTITY, payload_a)["Ref_Key"]
    finish("A_ib_base64_full", ref_a)

    # B: Volume meta + Base64 (no stream) — BSP/extension may move bytes to tom
    path_b = format_volume_file_path(ts, f"{base_name}_B.msg")
    payload_b = {
        "Description": f"{base_name}_B",
        "Расширение": ext,
        "ВладелецФайла_Key": doc_ref,
        "ТипХраненияФайла": "ВТомахНаДиске",
        "Том_Key": settings.odata_file_volume_key or VOL,
        "ПутьКФайлу": path_b,
        "Размер": len(msg),
        "ДатаСоздания": format_attached_file_created_at(ts),
        "ДатаМодификацииУниверсальная": format_attached_file_modified_universal(ts),
        "ФайлХранилище_Base64Data": base64.b64encode(msg).decode("ascii"),
        "ФайлХранилище_Type": "application/xml+xdto",
    }
    if author:
        payload_b["Автор_Key"] = author
        payload_b["Изменил_Key"] = author
    ref_b = client.create_entity(ENTITY, payload_b)["Ref_Key"]
    finish("B_volume_meta_plus_base64", ref_b)

    # C: Volume meta only + stream PUT
    path_c = format_volume_file_path(ts, f"{base_name}_C.msg")
    payload_c = {
        "Description": f"{base_name}_C",
        "Расширение": ext,
        "ВладелецФайла_Key": doc_ref,
        "ТипХраненияФайла": "ВТомахНаДиске",
        "Том_Key": settings.odata_file_volume_key or VOL,
        "ПутьКФайлу": path_c,
        "Размер": len(msg),
        "ДатаСоздания": format_attached_file_created_at(ts),
        "ДатаМодификацииУниверсальная": format_attached_file_modified_universal(ts),
        "ФайлХранилище_Type": "application/xml+xdto",
    }
    if author:
        payload_c["Автор_Key"] = author
        payload_c["Изменил_Key"] = author
    ref_c = client.create_entity(ENTITY, payload_c)["Ref_Key"]
    client.put_entity_stream(
        ENTITY,
        ref_c,
        "ФайлХранилище",
        msg,
        content_type="application/xml+xdto",
    )
    finish("C_volume_stream_put", ref_c)

    # D: IB Base64 then PATCH same Base64 again (retrigger handlers)
    payload_d = {
        "Description": f"{base_name}_D",
        "Расширение": ext,
        "ВладелецФайла_Key": doc_ref,
        "ТипХраненияФайла": "ВИнформационнойБазе",
        "Размер": len(msg),
        "ДатаСоздания": format_attached_file_created_at(ts),
        "ДатаМодификацииУниверсальная": format_attached_file_modified_universal(ts),
        "ФайлХранилище_Base64Data": base64.b64encode(msg).decode("ascii"),
        "ФайлХранилище_Type": "application/octet-stream",
    }
    if author:
        payload_d["Автор_Key"] = author
        payload_d["Изменил_Key"] = author
    ref_d = client.create_entity(ENTITY, payload_d)["Ref_Key"]
    client.patch_entity(
        ENTITY,
        ref_d,
        {
            "ФайлХранилище_Base64Data": base64.b64encode(msg).decode("ascii"),
            "ФайлХранилище_Type": "application/octet-stream",
            "Размер": len(msg),
        },
    )
    finish("D_ib_base64_repatch", ref_d)

    # E: POST meta only, PATCH Base64, then PATCH ТипХранения + size
    payload_e = {
        "Description": f"{base_name}_E",
        "Расширение": ext,
        "ВладелецФайла_Key": doc_ref,
        "Размер": len(msg),
        "ДатаСоздания": format_attached_file_created_at(ts),
        "ДатаМодификацииУниверсальная": format_attached_file_modified_universal(ts),
    }
    ref_e = client.create_entity(ENTITY, payload_e)["Ref_Key"]
    client.patch_entity(
        ENTITY,
        ref_e,
        {
            "ФайлХранилище_Base64Data": base64.b64encode(msg).decode("ascii"),
            "ФайлХранилище_Type": "application/octet-stream",
            "Размер": len(msg),
        },
    )
    client.patch_entity(
        ENTITY,
        ref_e,
        {
            "ТипХраненияФайла": "ВИнформационнойБазе",
            "Размер": len(msg),
        },
    )
    finish("E_twophase_then_set_kind", ref_e)

    # F: attach_file helper database mode via field_map override
    fm_db = {
        **fm,
        "defaults": {
            **(fm.get("defaults") or {}),
            "storage_mode": "database",
            "storage_kind": "ВИнформационнойБазе",
            "omit_storage_kind": False,
            "volume_preupload": False,
            "verify_mode": "bytes",
            "minimal_payload": False,
            "upload_binary_via_stream": False,
        },
    }
    res_f = attach_file_to_incoming_document(
        client,
        document_ref_key=doc_ref,
        file_input=AttachedFileInput(
            filename=f"{base_name}_F.msg",
            content=msg,
            author_key=author or None,
            edited_by_key=author or None,
            processed_at=ts,
        ),
        field_map=fm_db,
        document_number=DOC,
        message_id=email.message_id,
    )
    finish("F_helper_ib_full", res_f.ref_key)

    out = ROOT / "data" / "temp" / f"strategies_{DOC}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {
                "doc": DOC,
                "doc_ref": doc_ref,
                "msg_size": len(msg),
                "author": author,
                "interesting_entities": interesting,
                "results": results,
                "user_check": (
                    "В 1С откройте файлы с суффиксами _A…_F на документе "
                    f"{DOC} и напишите, какая буква открылась."
                ),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print("=== saved", out)
    print("Open in 1C attachments named", DOC + "_A … _F")


if __name__ == "__main__":
    main()
