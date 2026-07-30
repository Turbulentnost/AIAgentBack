"""Deep-dive: where Акинина stores destination on incoming docs."""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path
from urllib.parse import quote

import httpx

ROOT = Path("/app") if Path("/app/src/agent_pochta").exists() else Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_pochta.config import get_settings  # noqa: E402

USER_KEY = "7a3fa603-0899-11f0-9637-6cb31113810e"
DOC = "Document_ТД_ВходящаяКорреспонденция"
EMPTY = "00000000-0000-0000-0000-000000000000"


def get_json(client: httpx.Client, url: str) -> dict:
    r = client.get(url, timeout=180)
    r.raise_for_status()
    return r.json()


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    settings = get_settings()
    base = settings.odata_base_url.rstrip("/") + "/"
    auth = (settings.odata_username, settings.odata_password)

    with httpx.Client(auth=auth) as client:
        flt = f"Ответственный_Key eq guid'{USER_KEY}'"
        docs = get_json(
            client,
            f"{base}{quote(DOC)}?$format=json&$filter={quote(flt)}&$orderby=Date desc&$top=5",
        )["value"]

        samples = []
        for d in docs:
            ref = d["Ref_Key"]
            # full raw keys
            nonempty = {
                k: v
                for k, v in d.items()
                if v not in (None, "", [], {}, False)
                and not (isinstance(v, str) and (v == EMPTY or "navigationLink" in k))
                and k not in ("DataVersion",)
            }
            # try explicit OpenType-ish selects
            extra = {}
            for field in (
                "Подразделение",
                "Автор",
                "Кому",
                "Содержание",
                "ТемаСлужебнойЗаписки",
                "Направление",
                "ПлательщикНаправление",
                "Статус",
                "ИсточникПоступления",
                "ДокументОснование",
                "Претензия",
                "Комментарий",
                "ID_XML",
            ):
                try:
                    url = f"{base}{quote(DOC)}(guid'{ref}')?$format=json&$select={quote(field)}"
                    extra[field] = get_json(client, url).get(field)
                except Exception as exc:
                    extra[field] = f"ERR:{exc}"

            # tabular CRM_Исполнители
            tab = {}
            for nav in ("CRM_Исполнители", "ДополнительныеРеквизиты"):
                try:
                    url = f"{base}{quote(DOC)}(guid'{ref}')/{quote(nav)}?$format=json"
                    tab[nav] = get_json(client, url).get("value", [])
                except Exception as exc:
                    tab[nav] = [{"error": str(exc)[:200]}]

            # tasks / BP by subject
            linked = {}
            for ent, pred_field in (
                ("Task_ЗадачаИсполнителя", "Предмет"),
                ("BusinessProcess_Задание", "Предмет"),
                ("BusinessProcess_CRM_БизнесПроцесс", "Предмет"),
            ):
                try:
                    # filter by Predmet guid may need Type
                    flt2 = f"{pred_field} eq cast(guid'{ref}','Edm.String')"
                    # try several filter styles
                    found = []
                    for f in (
                        f"{pred_field} eq guid'{ref}'",
                        f"substringof('{ref}', {pred_field})",
                    ):
                        try:
                            url = f"{base}{quote(ent)}?$format=json&$filter={quote(f)}&$top=10"
                            found = get_json(client, url).get("value", [])
                            if found:
                                break
                        except Exception:
                            continue
                    # fallback: scan recent for this ref
                    if not found:
                        url = f"{base}{quote(ent)}?$format=json&$orderby=Date desc&$top=300"
                        items = get_json(client, url).get("value", [])
                        found = [
                            it
                            for it in items
                            if str(it.get("Предмет") or "") == ref
                            or str(it.get("Предмет") or "").lower() == ref.lower()
                        ]
                    slim = []
                    for it in found[:5]:
                        slim.append(
                            {
                                k: it.get(k)
                                for k in it
                                if any(
                                    x in k
                                    for x in (
                                        "Number",
                                        "Date",
                                        "Исполнит",
                                        "Роль",
                                        "Наименование",
                                        "Description",
                                        "Подразделен",
                                        "Completed",
                                        "Executed",
                                        "Предмет",
                                        "Тема",
                                        "Направлен",
                                        "Ответствен",
                                        "Автор",
                                        "Срок",
                                    )
                                )
                                or k in ("Number", "Date", "Ref_Key", "Completed", "Executed")
                            }
                        )
                    linked[ent] = {"count": len(found), "samples": slim}
                except Exception as exc:
                    linked[ent] = {"error": str(exc)[:300]}

            samples.append(
                {
                    "Number": d.get("Number"),
                    "Date": d.get("Date"),
                    "nonempty_keys": sorted(nonempty.keys()),
                    "nonempty": {k: (v if not isinstance(v, str) or len(v) < 300 else v[:300]) for k, v in nonempty.items()},
                    "extra_select": extra,
                    "tabular": tab,
                    "linked_processes": linked,
                }
            )

        # Aggregate Направление / Статус / Источник over 100
        docs100 = get_json(
            client,
            f"{base}{quote(DOC)}?$format=json&$filter={quote(flt)}&$orderby=Date desc&$top=100",
        )["value"]
        agg = {
            "Направление": Counter(d.get("Направление") or "(пусто)" for d in docs100),
            "Статус": Counter(d.get("Статус") or "(пусто)" for d in docs100),
            "ИсточникПоступления": Counter(d.get("ИсточникПоступления") or "(пусто)" for d in docs100),
            "ПлательщикНаправление": Counter(d.get("ПлательщикНаправление") or "(пусто)" for d in docs100),
            "has_Кому": sum(1 for d in docs100 if (d.get("Кому") or "").strip()),
            "has_exec_key": sum(
                1
                for d in docs100
                if (d.get("ПодразделениеИсполнитель_Key") or EMPTY) != EMPTY
            ),
            "has_assignee_key": sum(
                1
                for d in docs100
                if (d.get("КомуПодразделениеСсылка_Key") or EMPTY) != EMPTY
            ),
            "has_email_from": sum(1 for d in docs100 if (d.get("EmailОтправителяПисьма") or "").strip()),
            "has_partner": sum(1 for d in docs100 if (d.get("Партнер") or "").strip()),
            "theme_nonempty": sum(1 for d in docs100 if (d.get("ТемаСлужебнойЗаписки") or "").strip()),
        }

        # Compare one agent-created doc (Ответственный = defaults) vs Акинина
        ai_key = "a5e55eea-3a0a-11f0-9679-6cb31113810c"
        ai_flt = f"Ответственный_Key eq guid'{ai_key}'"
        ai_docs = get_json(
            client,
            f"{base}{quote(DOC)}?$format=json&$filter={quote(ai_flt)}&$orderby=Date desc&$top=2",
        )["value"]
        ai_sample = []
        for d in ai_docs:
            ai_sample.append(
                {
                    "Number": d.get("Number"),
                    "Кому": d.get("Кому"),
                    "ПодразделениеИсполнитель_Key": d.get("ПодразделениеИсполнитель_Key"),
                    "EmailОтправителяПисьма": d.get("EmailОтправителяПисьма"),
                    "Направление": d.get("Направление"),
                    "ТемаСлужебнойЗаписки": d.get("ТемаСлужебнойЗаписки"),
                    "Ответственный_Key": d.get("Ответственный_Key"),
                }
            )

        report = {
            "samples": samples,
            "aggregate_100": {k: (dict(v) if isinstance(v, Counter) else v) for k, v in agg.items()},
            "ai_contrast_samples": ai_sample,
        }
        print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
