"""Compare author/editor fields: manual Outlook vs agent uploads on 762."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.parse import quote

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_pochta.config import get_settings  # noqa: E402

ENTITY = "Catalog_ТД_ВходящаяКорреспонденцияПрисоединенныеФайлы"
REFS = {
    "manual_outlook": "598a6fa7-8759-11f1-984c-6cb31113810e",
    "agent_msg": "07a33c90-8757-11f1-984c-6cb31113810e",
    "agent_pdf": "07a33caf-8757-11f1-984c-6cb31113810e",
    "760_ok": "27997dc5-8689-11f1-984a-6cb31113810e",
}
AUTHOR = "a5e55eea-3a0a-11f0-9679-6cb31113810c"
KEY_FIELDS = [
    "Ref_Key",
    "Description",
    "Расширение",
    "Размер",
    "ДатаСоздания",
    "ДатаМодификацииУниверсальная",
    "Автор_Key",
    "Изменил_Key",
    "Редактирует_Key",
    "ТипХраненияФайла",
    "Том_Key",
    "ПутьКФайлу",
    "DeletionMark",
    "ИндексКартинки",
]
NAV = ["Автор", "Изменил", "Редактирует"]


def nav_get(base: str, auth, ref: str, nav: str) -> dict:
    url = f"{base}{quote(ENTITY)}(guid'{ref}')/{quote(nav)}?$format=json"
    resp = httpx.get(url, auth=auth, timeout=60)
    if resp.status_code == 200:
        data = resp.json()
        return {
            "status": 200,
            "Description": data.get("Description"),
            "Ref_Key": data.get("Ref_Key"),
        }
    return {"status": resp.status_code, "body": resp.text[:200]}


def author_lookup(base: str, auth, key: str) -> dict:
    for entity in (
        "Catalog_Пользователи",
        "Catalog_ПользователиOData",
        "Catalog_Пользователи1С",
    ):
        url = f"{base}{quote(entity)}(guid'{key}')?$format=json"
        resp = httpx.get(url, auth=auth, timeout=30)
        if resp.status_code == 200:
            data = resp.json()
            return {
                "entity": entity,
                "Description": data.get("Description"),
                "DeletionMark": data.get("DeletionMark"),
            }
    return {"status": "not_found"}


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    settings = get_settings()
    base = settings.odata_base_url.rstrip("/") + "/"
    auth = (settings.odata_username, settings.odata_password)

    report: dict = {
        "author_key_config": AUTHOR,
        "author_lookup": author_lookup(base, auth, AUTHOR),
        "attachments": {},
    }

    for label, ref in REFS.items():
        url = f"{base}{quote(ENTITY)}(guid'{ref}')?$format=json"
        resp = httpx.get(url, auth=auth, timeout=60)
        entry: dict = {"ref": ref, "get_status": resp.status_code}
        if resp.status_code == 200:
            data = resp.json()
            entry["fields"] = {k: data.get(k) for k in KEY_FIELDS}
            entry["extra_author_fields"] = {
                k: data.get(k)
                for k in data
                if any(x in k for x in ("Автор", "Измен", "Редакт", "Отредакт"))
            }
            entry["nav"] = {n: nav_get(base, auth, ref, n) for n in NAV}
        else:
            entry["error"] = resp.text[:500]
        report["attachments"][label] = entry

    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
