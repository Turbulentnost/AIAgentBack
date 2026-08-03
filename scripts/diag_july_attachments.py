"""Compare agent-created attachments (July 2026) for working vs broken docs."""
from __future__ import annotations

import json
import sys
from datetime import datetime
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

ENTITY = "Catalog_ТД_ВходящаяКорреспонденцияПрисоединенныеФайлы"
DOC_ENTITY = "Document_ТД_ВходящаяКорреспонденция"
NUMBERS = ["АЛ00-000760", "АЛ00-000762", "НП00-003877", "НП00-003884"]
META = [
    "Ref_Key",
    "Description",
    "Расширение",
    "Размер",
    "ТипХраненияФайла",
    "ФайлХранилище_Type",
    "ДатаСоздания",
    "ДатаМодификацииУниверсальная",
    "Автор_Key",
    "Редактировал_Key",
    "Редактирует_Key",
    "Том_Key",
    "ПутьКФайлу",
    "ПодписанЭП",
    "Зашифрован",
    "ИндексКартинки",
    "ХранитьВерсии",
    "СтатусИзвлеченияТекста",
    "ТекстХранилище",
]


def find_recent_docs(base: str, auth, number: str) -> list[dict]:
    url = (
        f"{base}{quote(DOC_ENTITY)}?$format=json"
        f"&$filter={quote(f'Number eq {chr(39)}{number}{chr(39)}')}"
        f"&$orderby=Date desc&$top=10"
    )
    return httpx.get(url, auth=auth, timeout=120).json().get("value", [])


def list_attachments(base: str, auth, owner: str) -> list[dict]:
    flt = f"ВладелецФайла_Key eq guid'{owner}'"
    url = (
        f"{base}{quote(ENTITY)}?$format=json"
        f"&$filter={quote(flt)}&$orderby=ДатаСоздания desc&$top=20"
    )
    return httpx.get(url, auth=auth, timeout=120).json().get("value", [])


def analyze_content(content: bytes, ext: str) -> dict:
    info: dict = {
        "size": len(content),
        "size_mod_512": len(content) % 512,
        "size_mod_1024": len(content) % 1024,
        "is_msg": is_msg_bytes(content),
    }
    if ext == "eml" or (content and not is_msg_bytes(content)):
        try:
            msg = BytesParser(policy=policy.default).parsebytes(content)
            atts = []
            for part in msg.walk():
                fn = part.get_filename()
                disp = (part.get_content_disposition() or "").lower()
                if fn or disp == "attachment":
                    payload = part.get_payload(decode=True) or b""
                    atts.append(
                        {
                            "name": fn,
                            "size": len(payload),
                            "pdf": payload[:5] == b"%PDF-",
                        }
                    )
            info["eml"] = {
                "subject": msg.get("Subject"),
                "from": msg.get("From"),
                "attachments": atts,
            }
        except Exception as exc:
            info["eml_error"] = str(exc)
    if ext == "msg" or is_msg_bytes(content):
        path = ROOT / "data" / "temp" / "compare_762" / "_tmp.msg"
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
                        "name": getattr(a, "display_name", None),
                        "size": len(data),
                        "pdf": data[:5] == b"%PDF-" if data else False,
                    }
                )
            info["msg"] = {"subject": m.subject, "attachments": atts}
        except Exception as exc:
            info["msg_error"] = str(exc)
    return info


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    settings = get_settings()
    base = settings.odata_base_url.rstrip("/") + "/"
    auth = (settings.odata_username, settings.odata_password)
    fm = load_attached_file_field_map()
    client = ODataClient(
        settings.odata_base_url,
        username=settings.odata_username,
        password=settings.odata_password,
        timeout_sec=120,
    )
    out_dir = ROOT / "data" / "temp" / "compare_762"
    out_dir.mkdir(parents=True, exist_ok=True)

    report: dict = {}
    for number in NUMBERS:
        docs = find_recent_docs(base, auth, number)
        doc_entry = {"all_docs": []}
        for doc in docs:
            ref = doc.get("Ref_Key")
            date = doc.get("Date")
            posted = doc.get("Posted")
            items = list_attachments(base, auth, ref) if ref else []
            files = []
            for item in items:
                fref = item.get("Ref_Key", "")
                content = (
                    read_attached_file_storage_bytes(
                        client, entity=ENTITY, ref_key=fref, field_map=fm
                    )
                    if fref
                    else b""
                )
                ext = (item.get("Расширение") or "").strip().lower()
                desc = (item.get("Description") or "").strip()
                fname = f"{desc}.{ext}" if ext else desc
                tag = f"{number}_{date}_{fname}".replace(":", "-")
                save = out_dir / tag
                if content:
                    save.write_bytes(content)
                files.append(
                    {
                        "meta": {k: item.get(k) for k in META},
                        "content": analyze_content(content, ext),
                        "saved": str(save) if content else None,
                    }
                )
            doc_entry["all_docs"].append(
                {
                    "doc_ref": ref,
                    "date": date,
                    "posted": posted,
                    "files": files,
                }
            )
        report[number] = doc_entry

    # Compare field names in metadata vs our map
    meta_url = f"{base}$metadata"
    meta_text = httpx.get(meta_url, auth=auth, timeout=60).text
    marker = f'EntityType Name="{ENTITY}"'
    idx = meta_text.find(marker)
    block = meta_text[idx : idx + 12000] if idx >= 0 else ""
    import re

    report["_metadata_fields"] = re.findall(r'Property Name="([^"]+)"', block)
    report["_wrong_field"] = {
        "we_send": "Редактировал_Key",
        "odata_has": "Редактирует_Key" in report["_metadata_fields"],
        "odata_has_wrong": "Редактировал_Key" in report["_metadata_fields"],
    }

    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
