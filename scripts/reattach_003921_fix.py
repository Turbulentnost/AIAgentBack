"""Reattach НП00-003921 without hard DELETE (OData DELETE returns 500)."""
from __future__ import annotations

import base64
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
    AttachedFileError,
    AttachedFileInput,
    attach_file_to_incoming_document,
    format_attached_file_created_at,
    format_attached_file_modified_universal,
    format_volume_file_path,
    list_attached_files_for_document,
    load_attached_file_field_map,
    now_attached_file_processed_at,
    read_attached_file_storage_bytes,
    release_attached_file_edit_lock,
    split_filename,
    verify_attached_file_storage,
)
from agent_pochta.services.odata_client import ODataClient  # noqa: E402
from agent_pochta.services.vault import StubVaultClient  # noqa: E402

DOC = "НП00-003921"
EMPTY = "00000000-0000-0000-0000-000000000000"
VOL = "21886495-364e-11ea-82f2-ac1f6b05524c"
ENTITY = "Catalog_ТД_ВходящаяКорреспонденцияПрисоединенныеФайлы"


def soft_delete(client: ODataClient, refs: list[str]) -> list[str]:
    done = []
    for ref in refs:
        if not ref:
            continue
        try:
            client.patch_entity(ENTITY, ref, {"DeletionMark": True})
            done.append(ref)
        except Exception as exc:
            print(f"soft_delete_fail {ref}: {exc}")
    return done


def score(meta: dict, content: bytes, stream: bytes) -> dict:
    kind = str(meta.get("ТипХраненияФайла") or "").strip()
    path = str(meta.get("ПутьКФайлу") or "").strip()
    tom = str(meta.get("Том_Key") or "").strip()
    b64 = meta.get("ФайлХранилище_Base64Data") or ""
    b64_len = len(b64) if isinstance(b64, str) else 0
    size = str(meta.get("Размер") or "")
    ghost = kind == "ВТомахНаДиске" and (not path or not tom or tom == EMPTY)
    openable_guess = (
        (kind == "ВТомахНаДиске" and bool(path) and tom not in ("", EMPTY))
        or (kind == "ВИнформационнойБазе" and (b64_len > 0 or len(stream) > 0))
    )
    return {
        "kind": kind,
        "tom": tom,
        "path": path,
        "size": size,
        "size_ok": size == str(len(content)),
        "b64_len": b64_len,
        "stream_len": len(stream),
        "ghost_volume": ghost,
        "openable_guess": openable_guess,
        "DeletionMark": meta.get("DeletionMark"),
        "Изменил_Key": meta.get("Изменил_Key"),
        "Description": meta.get("Description"),
    }


def finish(client, fm, label, ref, msg, verify_err=None):
    release_attached_file_edit_lock(client, entity=ENTITY, ref_key=ref, field_map=fm)
    meta = client.get_by_key(ENTITY, ref) or {}
    stream = read_attached_file_storage_bytes(
        client, entity=ENTITY, ref_key=ref, field_map=fm
    )
    item = {
        "strategy": label,
        "ref": ref,
        "verify_err": verify_err,
        **score(meta, msg, stream),
    }
    print(json.dumps(item, ensure_ascii=False))
    return item


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
    settings = get_settings()
    client = ODataClient(
        settings.odata_base_url,
        username=settings.odata_username,
        password=settings.odata_password,
        timeout_sec=180,
    )
    fm = load_attached_file_field_map()
    vol_key = settings.odata_file_volume_key or VOL

    existing = list_attached_files_for_document(
        client, document_ref_key=doc_ref, field_map=fm
    )
    old_refs = [
        str(i.get("Ref_Key"))
        for i in existing
        if str(i.get("Description") or "").startswith(DOC) and i.get("Ref_Key")
    ]
    soft_deleted = soft_delete(client, old_refs)

    results = []

    # S1: helper with explicit IB kind
    fm_ib = {
        **fm,
        "defaults": {
            **(fm.get("defaults") or {}),
            "storage_mode": "database",
            "storage_kind": "ВИнформационнойБазе",
            "omit_storage_kind": False,
            "minimal_payload": True,
            "volume_preupload": False,
            "upload_binary_via_stream": False,
            "verify_mode": "bsp_exchange",
        },
    }
    try:
        res = attach_file_to_incoming_document(
            client,
            document_ref_key=doc_ref,
            file_input=AttachedFileInput(
                filename=msg_name,
                content=msg,
                processed_at=ts,
            ),
            field_map=fm_ib,
            document_number=DOC,
            message_id=email.message_id,
        )
        results.append(finish(client, fm, "S1_helper_ib_explicit_kind", res.ref_key, msg))
    except AttachedFileError as exc:
        rows = list_attached_files_for_document(
            client, document_ref_key=doc_ref, field_map=fm
        )
        newest = sorted(
            [
                r
                for r in rows
                if str(r.get("Description") or "") == DOC
                and not r.get("DeletionMark")
            ],
            key=lambda r: str(r.get("ДатаСоздания") or ""),
            reverse=True,
        )
        if newest:
            results.append(
                finish(
                    client,
                    fm,
                    "S1_helper_ib_explicit_kind",
                    str(newest[0]["Ref_Key"]),
                    msg,
                    verify_err=str(exc),
                )
            )
        else:
            results.append(
                {"strategy": "S1_helper_ib_explicit_kind", "ref": None, "verify_err": str(exc)}
            )
            print(json.dumps(results[-1], ensure_ascii=False))

    # Soft-delete S1 if ghost so S2 can use clean Description
    s1 = results[-1] if results else {}
    if s1.get("ghost_volume") and s1.get("ref"):
        soft_delete(client, [s1["ref"]])

    # S2: volume Tom+Path + Base64
    # Use distinct Description if S1 still occupies DOC name without deletion
    desc2 = DOC if (s1.get("ghost_volume") or not s1.get("ref")) else f"{DOC}_V"
    path = format_volume_file_path(ts, f"{desc2}.msg")
    payload_s2 = {
        "Description": desc2,
        "Расширение": ext,
        "ВладелецФайла_Key": doc_ref,
        "ТипХраненияФайла": "ВТомахНаДиске",
        "Том_Key": vol_key,
        "ПутьКФайлу": path,
        "Размер": len(msg),
        "ДатаСоздания": format_attached_file_created_at(ts),
        "ДатаМодификацииУниверсальная": format_attached_file_modified_universal(ts),
        "ФайлХранилище_Base64Data": base64.b64encode(msg).decode("ascii"),
        "ФайлХранилище_Type": "application/xml+xdto",
    }
    created = client.create_entity(ENTITY, payload_s2)
    ref_s2 = created["Ref_Key"]
    try:
        verify_attached_file_storage(
            client,
            entity=ENTITY,
            ref_key=ref_s2,
            expected_size=len(msg),
            field_map={
                **fm,
                "defaults": {**(fm.get("defaults") or {}), "verify_mode": "bsp_exchange"},
            },
        )
        verr = None
    except AttachedFileError as exc:
        verr = str(exc)
    s2 = finish(client, fm, "S2_volume_path_plus_base64", ref_s2, msg, verify_err=verr)
    results.append(s2)

    # S2b: if path was cleared by extension, PATCH it back
    if s2.get("ghost_volume") or not s2.get("path"):
        try:
            client.patch_entity(
                ENTITY,
                ref_s2,
                {
                    "ТипХраненияФайла": "ВТомахНаДиске",
                    "Том_Key": vol_key,
                    "ПутьКФайлу": path,
                },
            )
            results.append(
                finish(
                    client,
                    fm,
                    "S2b_patch_tom_path",
                    ref_s2,
                    msg,
                    verify_err="metadata patched; disk file may still be missing",
                )
            )
        except Exception as exc:
            results.append(
                {
                    "strategy": "S2b_patch_tom_path",
                    "ref": ref_s2,
                    "verify_err": str(exc),
                }
            )

    # S3: IB Base64 left in place (no extension clear) — raw create without relying on BSP clear
    payload_s3 = {
        "Description": f"{DOC}_IB",
        "Расширение": ext,
        "ВладелецФайла_Key": doc_ref,
        "ТипХраненияФайла": "ВИнформационнойБазе",
        "Размер": len(msg),
        "ДатаСоздания": format_attached_file_created_at(ts),
        "ДатаМодификацииУниверсальная": format_attached_file_modified_universal(ts),
        "ФайлХранилище_Base64Data": base64.b64encode(msg).decode("ascii"),
        "ФайлХранилище_Type": "application/octet-stream",
    }
    ref_s3 = client.create_entity(ENTITY, payload_s3)["Ref_Key"]
    results.append(finish(client, fm, "S3_ib_base64_keep", ref_s3, msg))

    # Pick best recommendation
    recommended = None
    for item in results:
        if item.get("openable_guess") and item.get("ref") and not item.get("DeletionMark"):
            recommended = item
            break
    if recommended is None:
        for item in results:
            if item.get("ref") and not item.get("ghost_volume"):
                recommended = item
                break
    if recommended is None:
        recommended = next((r for r in results if r.get("ref")), None)

    # Rename recommended to DOC if needed and soft-delete worse DOC clones
    keep = (recommended or {}).get("ref")
    if keep and (recommended or {}).get("Description") != DOC:
        try:
            client.patch_entity(ENTITY, keep, {"Description": DOC})
            recommended = finish(
                client, fm, "rename_to_DOC", keep, msg, verify_err="renamed Description"
            )
            results.append(recommended)
        except Exception as exc:
            print("rename_fail", exc)

    report = {
        "at_utc": datetime.now(timezone.utc).isoformat(),
        "doc": DOC,
        "doc_ref": doc_ref,
        "msg_size": len(msg),
        "soft_deleted": soft_deleted,
        "working_manual_example": {
            "ref": "0fd104ee-687f-11ec-87ab-ac1f6b05524d",
            "tom": VOL,
            "path": "20211229\\АЛ00-000760.msg",
        },
        "results": results,
        "recommended_ref": keep,
        "recommended": recommended,
        "bsl_required": True,
        "bsl_file": "data/onec_extension_odata_attach_fix.bsl",
    }
    out = ROOT / "data" / "temp" / f"reattach_{DOC}_fix_report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("=== REPORT", out)
    print(
        json.dumps(
            {"recommended_ref": keep, "recommended": recommended, "results": results},
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
