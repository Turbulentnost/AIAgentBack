"""List duplicate document numbers and compare 2026 АЛ00-000760 vs АЛ00-000762."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.parse import quote

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_pochta.config import get_settings  # noqa: E402

DOC_ENTITY = "Document_ТД_ВходящаяКорреспонденция"
FILE_ENTITY = "Catalog_ТД_ВходящаяКорреспонденцияПрисоединенныеФайлы"
NUMBERS = ["АЛ00-000760", "АЛ00-000762"]


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    settings = get_settings()
    base = settings.odata_base_url.rstrip("/") + "/"
    auth = (settings.odata_username, settings.odata_password)
    report: dict = {}

    for number in NUMBERS:
        url = (
            f"{base}{quote(DOC_ENTITY)}?$format=json"
            f"&$filter={quote(f'Number eq {chr(39)}{number}{chr(39)}')}"
            f"&$orderby=Date desc&$top=10"
        )
        docs = httpx.get(url, auth=auth, timeout=120).json().get("value", [])
        entries = []
        for doc in docs:
            owner = doc.get("Ref_Key", "")
            flt = f"ВладелецФайла_Key eq guid'{owner}'"
            furl = (
                f"{base}{quote(FILE_ENTITY)}?$format=json"
                f"&$filter={quote(flt)}&$orderby=ДатаСоздания desc&$top=10"
            )
            files = httpx.get(furl, auth=auth, timeout=120).json().get("value", [])
            entries.append(
                {
                    "ref": owner,
                    "date": doc.get("Date"),
                    "posted": doc.get("Posted"),
                    "status": doc.get("Статус"),
                    "deletion_mark": doc.get("DeletionMark"),
                    "source": doc.get("ИсточникПоступления"),
                    "attachments": [
                        {
                            "ref": f.get("Ref_Key"),
                            "name": f"{f.get('Description')}.{f.get('Расширение')}",
                            "size": f.get("Размер"),
                            "storage": f.get("ТипХраненияФайла"),
                            "created": f.get("ДатаСоздания"),
                        }
                        for f in files
                    ],
                }
            )
        report[number] = entries

    d760 = next((d for d in report.get("АЛ00-000760", []) if "2026" in str(d.get("date", ""))), {})
    d762 = next((d for d in report.get("АЛ00-000762", []) if "2026" in str(d.get("date", ""))), {})
    if d760 and d762:
        keys = sorted((set(d760) | set(d762)) - {"attachments", "ref"})
        report["doc_field_diff_2026"] = {
            k: {"760": d760.get(k), "762": d762.get(k)}
            for k in keys
            if d760.get(k) != d762.get(k)
        }

    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
