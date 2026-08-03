"""List and analyze all attachments for АЛ00-000762 vs working АЛ00-000760."""
from __future__ import annotations

import json
import sys
from email import policy
from email.parser import BytesParser
from pathlib import Path
from urllib.parse import quote

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_pochta.config import get_settings  # noqa: E402
from agent_pochta.services.email_msg import is_msg_bytes  # noqa: E402
from agent_pochta.services.odata_attached_file import (  # noqa: E402
    load_attached_file_field_map,
    read_attached_file_storage_bytes,
)
from agent_pochta.services.odata_client import ODataClient  # noqa: E402

DOCS = ["АЛ00-000760", "АЛ00-000762", "НП00-003877", "НП00-003884"]
META = [
    "Ref_Key",
    "Description",
    "Расширение",
    "Размер",
    "ТипХраненияФайла",
    "ФайлХранилище_Type",
    "ДатаСоздания",
    "Автор_Key",
    "Редактировал_Key",
    "Том_Key",
    "ПутьКФайлу",
    "ПодписанЭП",
    "Зашифрован",
    "ИндексКартинки",
    "ХранитьВерсии",
]


def doc_owner_key(base: str, auth, number: str) -> str | None:
    url = (
        f"{base}{quote('Document_ТД_ВходящаяКорреспонденция')}"
        f"?$format=json&$filter={quote(f'Number eq {chr(39)}{number}{chr(39)}')}"
        f"&$orderby=Date desc&$top=1"
    )
    items = httpx.get(url, auth=auth, timeout=120).json().get("value", [])
    return items[0].get("Ref_Key") if items else None


def analyze_eml(content: bytes) -> dict:
    msg = BytesParser(policy=policy.default).parsebytes(content)
    atts = []
    for part in msg.walk():
        disp = (part.get_content_disposition() or "").lower()
        fn = part.get_filename()
        if fn or disp == "attachment":
            payload = part.get_payload(decode=True) or b""
            atts.append(
                {
                    "name": fn,
                    "ctype": part.get_content_type(),
                    "size": len(payload),
                    "pdf_magic": payload[:5] == b"%PDF-",
                }
            )
    return {
        "subject": msg.get("Subject"),
        "from": msg.get("From"),
        "date": msg.get("Date"),
        "attachments": atts,
    }


def analyze_msg(content: bytes) -> dict:
    out_dir = ROOT / "data" / "temp" / "compare_762"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "_probe.msg"
    path.write_bytes(content)
    try:
        from aspose.email_foss import msg as msgmod

        m = msgmod.MapiMessage.from_file(str(path))
        atts = []
        for i, a in enumerate(getattr(m, "attachments", None) or []):
            data = getattr(a, "content_stream", None) or b""
            atts.append(
                {
                    "i": i,
                    "name": getattr(a, "display_name", None) or getattr(a, "long_file_name", None),
                    "size": len(data),
                    "pdf_magic": data[:5] == b"%PDF-" if data else False,
                }
            )
        return {
            "subject": getattr(m, "subject", "") or "",
            "attachments": atts,
        }
    except Exception as exc:
        return {"error": str(exc)}


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    settings = get_settings()
    base = settings.odata_base_url.rstrip("/") + "/"
    auth = (settings.odata_username, settings.odata_password)
    fm = load_attached_file_field_map()
    entity = fm["entity"]
    client = ODataClient(
        settings.odata_base_url,
        username=settings.odata_username,
        password=settings.odata_password,
        timeout_sec=120,
    )
    out_dir = ROOT / "data" / "temp" / "compare_762"
    out_dir.mkdir(parents=True, exist_ok=True)

    report: dict = {"docs": {}}
    for number in DOCS:
        owner = doc_owner_key(base, auth, number)
        if not owner:
            report["docs"][number] = {"error": "document not found"}
            continue
        flt = f"ВладелецФайла_Key eq guid'{owner}'"
        url = (
            f"{base}{quote(entity)}?$format=json"
            f"&$filter={quote(flt)}&$orderby=ДатаСоздания desc&$top=20"
        )
        items = httpx.get(url, auth=auth, timeout=120).json().get("value", [])
        files = []
        for item in items:
            ref = item.get("Ref_Key", "")
            content = (
                read_attached_file_storage_bytes(
                    client, entity=entity, ref_key=ref, field_map=fm
                )
                if ref
                else b""
            )
            ext = (item.get("Расширение") or "").strip().lower()
            desc = (item.get("Description") or "").strip()
            fname = f"{desc}.{ext}" if ext else desc
            save = out_dir / f"{number.replace('/', '_')}_{fname}"
            if content:
                save.write_bytes(content)
            parsed = {}
            if ext == "eml" and content:
                try:
                    parsed = analyze_eml(content)
                except Exception as exc:
                    parsed = {"error": str(exc)}
            elif ext == "msg" and content:
                parsed = analyze_msg(content)
            files.append(
                {
                    "meta": {k: item.get(k) for k in META},
                    "size_bytes": len(content),
                    "is_msg_ole": is_msg_bytes(content),
                    "saved": str(save) if content else None,
                    "parsed": parsed,
                }
            )
        report["docs"][number] = {"owner_ref": owner, "files": files, "count": len(files)}

    # Probe СведенияОФайлах if entity exists
    info_entity = "InformationRegister_СведенияОФайлах"
    probe_url = f"{base}{quote(info_entity)}?$format=json&$top=1"
    try:
        r = httpx.get(probe_url, auth=auth, timeout=30)
        report["svедения_o_файлах_accessible"] = r.status_code == 200
        if r.status_code == 200:
            report["svедения_sample_keys"] = list((r.json().get("value") or [{}])[0].keys())[:20]
    except Exception as exc:
        report["svедения_o_файлах_accessible"] = False
        report["svедения_error"] = str(exc)

    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
