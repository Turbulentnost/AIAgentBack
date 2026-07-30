"""Fast dump of one Акинина doc + aggregate destination fields."""
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

USER = "7a3fa603-0899-11f0-9637-6cb31113810e"
DOC = "Document_ТД_ВходящаяКорреспонденция"
EMPTY = "00000000-0000-0000-0000-000000000000"


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    s = get_settings()
    base = s.odata_base_url.rstrip("/") + "/"
    auth = (s.odata_username, s.odata_password)
    flt = f"Ответственный_Key eq guid'{USER}'"

    with httpx.Client(auth=auth, timeout=120) as c:
        docs = c.get(
            f"{base}{quote(DOC)}?$format=json&$filter={quote(flt)}&$orderby=Date desc&$top=100"
        ).raise_for_status().json()["value"]

        d0 = docs[0]
        ref = d0["Ref_Key"]
        nonempty = {}
        for k, v in sorted(d0.items()):
            if v in (None, "", [], {}) or (isinstance(v, str) and (v == EMPTY or "navigationLink" in k)):
                continue
            if k == "DataVersion":
                continue
            nonempty[k] = v if not isinstance(v, str) or len(v) < 300 else v[:300]

        selects = {}
        for field in (
            "Подразделение",
            "Автор",
            "Кому",
            "Направление",
            "ПлательщикНаправление",
            "ИсточникПоступления",
            "Комментарий",
            "ID_XML",
            "ДокументОснование",
            "ТемаСлужебнойЗаписки",
            "Содержание",
        ):
            r = c.get(f"{base}{quote(DOC)}(guid'{ref}')?$format=json&$select={quote(field)}")
            selects[field] = {
                "status": r.status_code,
                "value": r.json().get(field) if r.status_code == 200 else r.text[:200],
            }

        tabs = {}
        for nav in ("CRM_Исполнители",):
            r = c.get(f"{base}{quote(DOC)}(guid'{ref}')/{quote(nav)}?$format=json")
            if r.status_code == 200:
                tabs[nav] = r.json().get("value", [])[:5]
            else:
                tabs[nav] = {"status": r.status_code, "body": r.text[:200]}

        # Try register InformationRegister for performers?
        # Aggregate over 100
        agg = {
            "n": len(docs),
            "Направление": dict(Counter(x.get("Направление") or "(пусто)" for x in docs)),
            "Статус": dict(Counter(x.get("Статус") or "(пусто)" for x in docs)),
            "ИсточникПоступления": dict(Counter(x.get("ИсточникПоступления") or "(пусто)" for x in docs)),
            "ПлательщикНаправление": dict(Counter(x.get("ПлательщикНаправление") or "(пусто)" for x in docs)),
            "has_Кому": sum(1 for x in docs if (x.get("Кому") or "").strip()),
            "has_exec": sum(1 for x in docs if (x.get("ПодразделениеИсполнитель_Key") or EMPTY) != EMPTY),
            "has_assignee": sum(1 for x in docs if (x.get("КомуПодразделениеСсылка_Key") or EMPTY) != EMPTY),
            "has_from": sum(1 for x in docs if (x.get("EmailОтправителяПисьма") or "").strip()),
            "has_to": sum(1 for x in docs if (x.get("EmailПолучателяПисьма") or "").strip()),
            "has_partner": sum(1 for x in docs if (x.get("Партнер") or "").strip()),
            "has_theme": sum(1 for x in docs if (x.get("ТемаСлужебнойЗаписки") or "").strip()),
            "has_content": sum(1 for x in docs if (x.get("Содержание") or "").strip()),
        }

        # Look at metadata for properties containing Подраздел / Кому / Исполнит
        meta = c.get(f"{base}$metadata", timeout=180).text
        marker = 'EntityType Name="Document_ТД_ВходящаяКорреспонденция"'
        idx = meta.find(marker)
        block = ""
        if idx >= 0:
            end = meta.find("</EntityType>", idx)
            block = meta[idx:end]
        props = []
        import re
        for m in re.finditer(r'Property Name="([^"]+)"[^/]*/>', block):
            name = m.group(1)
            if any(x in name for x in ("Подраздел", "Кому", "Исполнит", "Автор", "Направ", "Ответствен", "Служб", "Роль")):
                props.append(name)
        navs = re.findall(r'NavigationProperty Name="([^"]+)"', block)

        # Resolve Направление labels via samples of themes per direction
        by_dir = {}
        for x in docs:
            nd = x.get("Направление") or "(пусто)"
            by_dir.setdefault(nd, []).append(
                {
                    "Number": x.get("Number"),
                    "theme": (x.get("ТемаСлужебнойЗаписки") or "")[:100],
                    "partner": x.get("Партнер"),
                    "status": x.get("Статус"),
                }
            )
        dir_samples = {k: v[:5] for k, v in sorted(by_dir.items(), key=lambda kv: -len(kv[1]))}

        # Check if agent has ANY of these numbers (maybe different DB host inside docker)
        from sqlalchemy import create_engine, text
        eng = create_engine(s.database_url)
        nums = [x.get("Number") for x in docs[:30] if x.get("Number")]
        with eng.connect() as conn:
            # how many erp docs total recently
            total_erp = conn.execute(text("SELECT count(*) FROM email_messages WHERE erp_document_number IS NOT NULL")).scalar()
            sample_erp = conn.execute(text(
                "SELECT erp_document_number, department_id, sender_email, subject FROM email_messages "
                "WHERE erp_document_number IS NOT NULL ORDER BY received_at DESC LIMIT 10"
            )).mappings().all()
            matched = conn.execute(
                text("SELECT erp_document_number, department_id, sender_email, subject FROM email_messages WHERE erp_document_number = ANY(:n)"),
                {"n": nums},
            ).mappings().all()
            # also try without prefix
            like_hits = conn.execute(
                text(
                    "SELECT erp_document_number, department_id FROM email_messages "
                    "WHERE erp_document_number LIKE 'НП00-0039%' OR erp_document_number LIKE 'АЛ00-0007%' "
                    "ORDER BY erp_document_number DESC LIMIT 20"
                )
            ).mappings().all()

        report = {
            "first_doc": {"Number": d0.get("Number"), "Ref_Key": ref, "nonempty": nonempty},
            "selects": selects,
            "tabs": tabs,
            "metadata_interesting_props": props,
            "metadata_navs_sample": [n for n in navs if any(x in n for x in ("Исполнит", "Подраздел", "Файл", "Задач", "Процесс", "Кому"))][:40],
            "aggregate_100": agg,
            "direction_samples": dir_samples,
            "agent_db": {
                "total_with_erp": total_erp,
                "recent_erp": [dict(r) for r in sample_erp],
                "matched_akinina_nums": [dict(r) for r in matched],
                "nearby_numbers": [dict(r) for r in like_hits],
            },
        }
        out = ROOT / "data" / "temp" / "akinina_destination_probe.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({
            "written": str(out),
            "first": d0.get("Number"),
            "agg": agg,
            "select_подраделение": selects.get("Подразделение"),
            "select_автор": selects.get("Автор"),
            "directions": agg["Направление"],
            "agent_matched": len(matched),
            "nearby": [dict(r) for r in like_hits[:10]],
            "props": props,
        }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
