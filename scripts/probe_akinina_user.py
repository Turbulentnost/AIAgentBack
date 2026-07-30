"""Find user Акинина in OData catalogs and sample linked incoming docs."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.parse import quote

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_pochta.config import get_settings  # noqa: E402

EMPTY = "00000000-0000-0000-0000-000000000000"
NEEDLE = "Акинин"
DOC = "Document_ТД_ВходящаяКорреспонденция"
USER_CATALOGS = (
    "Catalog_Пользователи",
    "Catalog_ВнешниеПользователи",
    "Catalog_Сотрудники",
    "Catalog_ФизическиеЛица",
)


def get_json(client: httpx.Client, url: str) -> dict:
    r = client.get(url, timeout=120)
    r.raise_for_status()
    return r.json()


def search_catalog(client: httpx.Client, base: str, entity: str) -> list[dict]:
    # substring filter may fail on some 1C builds — fall back to scan
    out: list[dict] = []
    try:
        # OData substringof / contains — try both styles
        for flt in (
            f"substringof('{NEEDLE}', Description)",
            f"contains(Description,'{NEEDLE}')",
            f"substringof('{NEEDLE}', Description) eq true",
        ):
            url = f"{base}{quote(entity)}?$format=json&$filter={quote(flt)}&$top=50"
            try:
                items = get_json(client, url).get("value", [])
                if items:
                    return items
            except httpx.HTTPStatusError:
                continue
    except Exception:
        pass

    # scan recent / first pages
    url = f"{base}{quote(entity)}?$format=json&$top=1000&$select=Ref_Key,Description,Code"
    try:
        items = get_json(client, url).get("value", [])
    except httpx.HTTPStatusError:
        url = f"{base}{quote(entity)}?$format=json&$top=500"
        items = get_json(client, url).get("value", [])
    for item in items:
        blob = " ".join(str(item.get(k) or "") for k in ("Description", "Code", "Наименование"))
        if NEEDLE.casefold() in blob.casefold():
            out.append(item)
    return out


def sample_doc_fields(client: httpx.Client, base: str) -> dict:
    url = f"{base}{quote(DOC)}?$format=json&$orderby=Date desc&$top=1"
    doc = get_json(client, url)["value"][0]
    userish = sorted(
        k
        for k in doc
        if any(
            n in k
            for n in (
                "Автор",
                "Ответствен",
                "Зарегистр",
                "Исполнит",
                "Изменил",
                "Кому",
                "Подраздел",
                "Служб",
                "Направлен",
                "Тема",
                "Партнер",
                "Организ",
                "Email",
                "Содержан",
                "Number",
                "Date",
                "Статус",
            )
        )
        or k in ("Number", "Date", "Posted", "DeletionMark", "Ref_Key")
    )
    return {"keys_sample": userish, "sample_doc_subset": {k: doc.get(k) for k in userish}}


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    settings = get_settings()
    base = settings.odata_base_url.rstrip("/") + "/"
    auth = (settings.odata_username, settings.odata_password)

    with httpx.Client(auth=auth) as client:
        catalogs: dict[str, list] = {}
        for ent in USER_CATALOGS:
            try:
                catalogs[ent] = [
                    {
                        "Ref_Key": i.get("Ref_Key"),
                        "Description": i.get("Description"),
                        "Code": i.get("Code"),
                        "keys": sorted(i.keys())[:40],
                    }
                    for i in search_catalog(client, base, ent)
                ]
            except Exception as exc:
                catalogs[ent] = [{"error": str(exc)}]

        fields = sample_doc_fields(client, base)
        report = {"needle": NEEDLE, "catalogs": catalogs, "doc_fields": fields}
        print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
