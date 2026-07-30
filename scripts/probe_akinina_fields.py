"""Probe which Document/Task fields reference Акинина and collect counts."""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path
from urllib.parse import quote

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_pochta.config import get_settings  # noqa: E402

USER_KEY = "7a3fa603-0899-11f0-9637-6cb31113810e"
EMP_KEY = "5c9b35fd-086f-11f0-9637-6cb31113810e"
PERSON_KEY = "c5d267fd-086e-11f0-9637-6cb31113810e"
DOC = "Document_ТД_ВходящаяКорреспонденция"
EMPTY = "00000000-0000-0000-0000-000000000000"


def get_json(client: httpx.Client, url: str) -> dict:
    r = client.get(url, timeout=180)
    r.raise_for_status()
    return r.json()


def try_filter(client: httpx.Client, base: str, entity: str, flt: str, top: int = 5) -> dict:
    url = f"{base}{quote(entity)}?$format=json&$filter={quote(flt)}&$orderby=Date desc&$top={top}"
    try:
        data = get_json(client, url)
        vals = data.get("value", [])
        return {
            "ok": True,
            "count_returned": len(vals),
            "numbers": [v.get("Number") for v in vals[:5]],
            "dates": [v.get("Date") for v in vals[:5]],
            "sample_keys_with_user": _keys_with_user(vals[0]) if vals else {},
        }
    except httpx.HTTPStatusError as exc:
        return {"ok": False, "status": exc.response.status_code, "body": exc.response.text[:300]}


def _keys_with_user(doc: dict) -> dict:
    hits = {}
    for k, v in doc.items():
        if v in (USER_KEY, EMP_KEY, PERSON_KEY) or (
            isinstance(v, str) and "Акинин" in v
        ):
            hits[k] = v
    return hits


def scan_recent_for_user(client: httpx.Client, base: str, top: int = 300) -> dict:
    url = f"{base}{quote(DOC)}?$format=json&$orderby=Date desc&$top={top}"
    docs = get_json(client, url).get("value", [])
    field_hits: Counter = Counter()
    matched = []
    # collect all keys that look user-related from first doc
    all_userish = sorted(
        k
        for k in (docs[0] if docs else {})
        if any(x in k for x in ("Автор", "Ответствен", "Зарегистр", "Исполнит", "Изменил", "Кому", "Регистр"))
        or k.endswith("_Key")
    )
    for doc in docs:
        hits = {}
        for k, v in doc.items():
            if v in (USER_KEY, EMP_KEY, PERSON_KEY):
                hits[k] = v
                field_hits[k] += 1
            elif isinstance(v, str) and "Акинин" in v:
                hits[k] = v
                field_hits[f"{k}(str)"] += 1
        if hits:
            matched.append(
                {
                    "Number": doc.get("Number"),
                    "Date": doc.get("Date"),
                    "Кому": doc.get("Кому"),
                    "hits": hits,
                    "Автор": doc.get("Автор"),
                    "Ответственный_Key": doc.get("Ответственный_Key"),
                }
            )
    return {
        "scanned": len(docs),
        "matched": len(matched),
        "field_hits": dict(field_hits),
        "userish_keys_on_doc": all_userish,
        "samples": matched[:15],
        "has_Автор_field": "Автор" in (docs[0] if docs else {}),
        "Автор_samples": [
            {"Number": d.get("Number"), "Автор": d.get("Автор"), "Ответственный_Key": d.get("Ответственный_Key")}
            for d in docs[:20]
        ],
    }


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    settings = get_settings()
    base = settings.odata_base_url.rstrip("/") + "/"
    auth = (settings.odata_username, settings.odata_password)

    with httpx.Client(auth=auth) as client:
        # Resolve user display / department
        user = get_json(
            client,
            f"{base}{quote('Catalog_Пользователи')}(guid'{USER_KEY}')?$format=json",
        )
        filters = {
            "Ответственный_Key": f"Ответственный_Key eq guid'{USER_KEY}'",
            "КомуПодразделение_skip": None,
        }
        # Try common key fields
        field_filters = {}
        for field in (
            "Ответственный_Key",
            "Автор_Key",
            "Зарегистрировал_Key",
            "Изменил_Key",
            "Исполнитель_Key",
        ):
            field_filters[field] = try_filter(
                client, base, DOC, f"{field} eq guid'{USER_KEY}'", top=5
            )

        # string Автор
        field_filters["Автор_substring"] = try_filter(
            client, base, DOC, "substringof('Акинин', Автор)", top=5
        )

        # employee key variants
        for field in ("Ответственный_Key",):
            field_filters[f"{field}_emp"] = try_filter(
                client, base, DOC, f"{field} eq guid'{EMP_KEY}'", top=3
            )

        scan = scan_recent_for_user(client, base, top=500)

        # Also check tasks
        task_scan = {}
        for ent in ("Task_ЗадачаИсполнителя", "BusinessProcess_Задание"):
            try:
                url = f"{base}{quote(ent)}?$format=json&$orderby=Date desc&$top=200"
                items = get_json(client, url).get("value", [])
                hits = []
                for it in items:
                    for k, v in it.items():
                        if v in (USER_KEY, EMP_KEY, PERSON_KEY) or (
                            isinstance(v, str) and "Акинин" in v
                        ):
                            hits.append({"Number": it.get("Number"), "field": k, "value": v, "Date": it.get("Date")})
                            break
                task_scan[ent] = {"scanned": len(items), "matched": len(hits), "samples": hits[:10]}
            except Exception as exc:
                task_scan[ent] = {"error": str(exc)}

        report = {
            "user": {
                "Ref_Key": USER_KEY,
                "Description": user.get("Description"),
                "Подразделение_Key": user.get("Подразделение_Key"),
                "Недействителен": user.get("Недействителен"),
            },
            "field_filters": field_filters,
            "recent_scan": scan,
            "task_scan": task_scan,
        }
        print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
