"""Diagnose НП00-003900 attachment vs working АЛ00-000760 reference."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_pochta.config import get_settings  # noqa: E402
from agent_pochta.services.odata_attached_file import (  # noqa: E402
    load_attached_file_field_map,
    read_attached_file_storage_bytes,
)
from agent_pochta.services.odata_client import ODataClient  # noqa: E402

ENTITY = "Catalog_ТД_ВходящаяКорреспонденцияПрисоединенныеФайлы"
CASES = {
    "3900-broken": "108c8480-8983-11f1-984f-6cb31113810c",
    "760-ok": "27997dc5-8689-11f1-984a-6cb31113810e",
    "877-ok": "278fa9aa-8675-11f1-984a-6cb31113810e",
    "manual-outlook-volume": "598a6fa7-8759-11f1-984c-6cb31113810e",
}
META = [
    "ТипХраненияФайла",
    "Том_Key",
    "ПутьКФайлу",
    "ФайлХранилище_Type",
    "Размер",
    "Редактирует_Key",
    "Изменил_Key",
    "Автор_Key",
    "DeletionMark",
    "Description",
    "Расширение",
    "ДатаСоздания",
    "ДатаМодификацииУниверсальная",
    "IsFolder",
    "ХранитьВерсии",
    "ПодписанЭП",
    "Зашифрован",
    "Описание",
    "Parent_Key",
    "ИндексКартинки",
    "ДатаЗаема",
    "СтатусИзвлеченияТекста",
    "ТекстХранилище_Type",
    "ТекстХранилище_Base64Data",
]


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    settings = get_settings()
    client = ODataClient(
        settings.odata_base_url,
        username=settings.odata_username,
        password=settings.odata_password,
        timeout_sec=120,
    )
    fm = load_attached_file_field_map()
    rows: dict[str, dict] = {}
    full_rows: dict[str, dict] = {}
    for label, ref in CASES.items():
        rec = client.get_by_key(ENTITY, ref) or {}
        stream = read_attached_file_storage_bytes(
            client, entity=ENTITY, ref_key=ref, field_map=fm
        )
        b64 = rec.get("ФайлХранилище_Base64Data") or ""
        rows[label] = {k: rec.get(k) for k in META}
        rows[label]["Ref_Key"] = ref
        rows[label]["_stream_len"] = len(stream)
        rows[label]["_b64_len"] = len(b64)
        rows[label]["_has_b64"] = bool(b64)
        skip = {"ФайлХранилище_Base64Data", "ТекстХранилище", "ФайлХранилище"}
        full_rows[label] = {
            k: v
            for k, v in rec.items()
            if k not in skip and not str(k).endswith("@navigationLinkUrl")
        }

    diff: dict[str, dict] = {}
    full_diff: dict[str, dict] = {}
    all_keys = set()
    full_keys: set[str] = set()
    for row in rows.values():
        all_keys.update(row.keys())
    for row in full_rows.values():
        full_keys.update(row.keys())
    for key in sorted(all_keys):
        if key.startswith("_"):
            continue
        values = {label: rows[label].get(key) for label in CASES}
        uniq = {json.dumps(v, ensure_ascii=False, sort_keys=True) for v in values.values()}
        if len(uniq) > 1:
            diff[key] = values
    for key in sorted(full_keys):
        values = {label: full_rows[label].get(key) for label in CASES if label in full_rows}
        present = {lbl: v for lbl, v in values.items() if v is not None}
        if len({json.dumps(v, ensure_ascii=False, sort_keys=True) for v in present.values()}) > 1:
            full_diff[key] = values

    report = {
        "diff_fields": diff,
        "full_diff_vs_877": {
            k: full_diff[k]
            for k in full_diff
            if full_diff[k].get("3900-broken") != full_diff[k].get("877-ok")
        },
        "storage": {
            lbl: {
                "stream_len": rows[lbl]["_stream_len"],
                "b64_len": rows[lbl]["_b64_len"],
                "meta_size": rows[lbl].get("Размер"),
                "record_exists": bool(full_rows.get(lbl)),
            }
            for lbl in CASES
        },
    }
    out_path = ROOT / "data" / "temp" / "diag_003900_report.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


def list_doc_attachments(doc_number: str) -> None:
    settings = get_settings()
    client = ODataClient(
        settings.odata_base_url,
        username=settings.odata_username,
        password=settings.odata_password,
        timeout_sec=120,
    )
    doc_entity = "Document_ТД_ВходящаяКорреспонденция"
    att_entity = ENTITY
    docs = client.fetch_filtered(doc_entity, filter_expr=f"Number eq '{doc_number}'")
    print(json.dumps({"doc_number": doc_number, "docs_found": len(docs)}, ensure_ascii=False))
    for doc in docs:
        doc_ref = doc.get("Ref_Key")
        atts = client.fetch_filtered(
            att_entity,
            filter_expr=f"ВладелецФайла_Key eq guid'{doc_ref}'",
        )
        print(
            json.dumps(
                {
                    "doc_ref": doc_ref,
                    "doc_date": doc.get("Date"),
                    "attachments": [
                        {
                            "Ref_Key": a.get("Ref_Key"),
                            "Description": a.get("Description"),
                            "Расширение": a.get("Расширение"),
                            "Размер": a.get("Размер"),
                            "ТипХраненияФайла": a.get("ТипХраненияФайла"),
                            "Редактирует_Key": a.get("Редактирует_Key"),
                            "DeletionMark": a.get("DeletionMark"),
                        }
                        for a in atts
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
        )


def compare_bytes_and_list_760() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    import base64

    settings = get_settings()
    client = ODataClient(
        settings.odata_base_url,
        username=settings.odata_username,
        password=settings.odata_password,
        timeout_sec=120,
    )
    fm = load_attached_file_field_map()
    att_entity = ENTITY
    doc_entity = "Document_ТД_ВходящаяКорреспонденция"
    report: dict[str, Any] = {"bytes": {}, "docs": {}}
    for label, ref in [("3900", CASES["3900-broken"]), ("877", CASES["877-ok"])]:
        rec = client.get_by_key(att_entity, ref) or {}
        stream = read_attached_file_storage_bytes(
            client, entity=att_entity, ref_key=ref, field_map=fm
        )
        b64 = rec.get("ФайлХранилище_Base64Data") or ""
        decoded = base64.b64decode(b64) if b64 else b""
        report["bytes"][label] = {
            "stream_len": len(stream),
            "b64_decoded_len": len(decoded),
            "stream_eq_b64": stream == decoded,
            "cfb_magic": stream[:8].hex() if len(stream) >= 8 else None,
            "Редактирует_Key": rec.get("Редактирует_Key"),
        }
    for num in ["АЛ00-000760", "НП00-003900"]:
        docs = client.fetch_filtered(doc_entity, filter_expr=f"Number eq '{num}'")
        entries = []
        for doc in docs:
            atts = client.fetch_filtered(
                att_entity,
                filter_expr=f"ВладелецФайла_Key eq guid'{doc.get('Ref_Key')}'",
            )
            entries.append(
                {
                    "doc_ref": doc.get("Ref_Key"),
                    "doc_date": doc.get("Date"),
                    "attachments": [
                        {
                            "Ref_Key": a.get("Ref_Key"),
                            "Description": a.get("Description"),
                            "Размер": a.get("Размер"),
                            "Редактирует_Key": a.get("Редактирует_Key"),
                            "ТипХраненияФайла": a.get("ТипХраненияФайла"),
                        }
                        for a in atts
                    ],
                }
            )
        report["docs"][num] = entries
    out = ROOT / "data" / "temp" / "diag_003900_bytes.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "list":
        for num in sys.argv[2:] or ["АЛ00-000760", "НП00-003900"]:
            list_doc_attachments(num)
    elif len(sys.argv) > 1 and sys.argv[1] == "bytes":
        compare_bytes_and_list_760()
    else:
        main()
