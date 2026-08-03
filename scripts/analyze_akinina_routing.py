"""Full analysis of ~100 incoming docs where Акинина is Ответственный."""
from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import quote

import httpx
from sqlalchemy import create_engine, text

ROOT = Path("/app") if Path("/app/src/agent_pochta").exists() else Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_pochta.config import get_settings  # noqa: E402

USER_KEY = "7a3fa603-0899-11f0-9637-6cb31113810e"
USER_NAME = "Акинина Татьяна Владимировна"
DOC = "Document_ТД_ВходящаяКорреспонденция"
EMPTY = "00000000-0000-0000-0000-000000000000"
OUT = ROOT / "data" / "temp" / "akinina_routing_analysis.json"
TOP_N = 120


def get_json(client: httpx.Client, url: str) -> dict:
    r = client.get(url, timeout=180)
    r.raise_for_status()
    return r.json()


def load_dept_maps(settings) -> tuple[dict[str, str], dict[str, str]]:
    """guid->code and code->name."""
    keys_path = Path(settings.odata_department_keys_file or ROOT / "data" / "odata_department_keys.json")
    if not keys_path.is_absolute():
        keys_path = ROOT / keys_path
    if not keys_path.exists():
        keys_path = ROOT / "data" / "odata_department_keys.json"
    code_by_guid = {v.lower(): k for k, v in json.loads(keys_path.read_text(encoding="utf-8")).items()}

    name_by_code: dict[str, str] = {}
    # from routing_rules exact_email + content
    rules_path = ROOT / "data" / "routing_rules.json"
    rules = json.loads(rules_path.read_text(encoding="utf-8"))
    for rule in rules.get("exact_email_rules", []):
        if rule.get("code") and rule.get("name"):
            name_by_code.setdefault(rule["code"], rule["name"])
    for rule in rules.get("content_rules", []):
        if rule.get("code") and rule.get("name"):
            name_by_code.setdefault(rule["code"], rule["name"])
    isr = rules.get("info_strict_rules", {})
    for block in isr.values():
        if isinstance(block, dict) and block.get("code") and block.get("name"):
            name_by_code.setdefault(block["code"], block["name"])

    # rag keywords file has only codes; try departments from qdrant dump / tz
    tz = ROOT / "data" / "tz_department_topics.json"
    if tz.exists():
        data = json.loads(tz.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            for code, meta in data.items():
                if not isinstance(meta, dict):
                    if isinstance(meta, str):
                        name_by_code.setdefault(code, meta)
                    continue
                if meta.get("name"):
                    name_by_code.setdefault(code, meta["name"])
                elif meta.get("names"):
                    name_by_code.setdefault(code, meta["names"][0])
                elif meta.get("topics"):
                    name_by_code.setdefault(code, meta["topics"][0])

    ui = ROOT / "data" / "ui_department_allowlist.json"
    if ui.exists():
        data = json.loads(ui.read_text(encoding="utf-8"))
        items = data if isinstance(data, list) else data.get("departments", data.get("items", []))
        if isinstance(items, list):
            for it in items:
                if isinstance(it, dict):
                    code = it.get("code") or it.get("department_id") or it.get("id")
                    name = it.get("name") or it.get("department_name")
                    if code and name:
                        name_by_code.setdefault(str(code), str(name))
        elif isinstance(data, dict):
            for code, name in data.items():
                if isinstance(name, str):
                    name_by_code.setdefault(str(code), name)

    return code_by_guid, name_by_code


def resolve_dept(
    doc: dict,
    code_by_guid: dict[str, str],
    name_by_code: dict[str, str],
    guid_names: dict[str, str],
) -> dict:
    komu = (doc.get("Кому") or "").strip()
    exec_key = (doc.get("ПодразделениеИсполнитель_Key") or "").strip().lower()
    assignee_key = (doc.get("КомуПодразделениеСсылка_Key") or "").strip().lower()

    code = ""
    source = ""
    if komu and re.match(r"^\d{2}-\d{6}$", komu):
        code = komu
        source = "Кому"
    elif exec_key and exec_key != EMPTY:
        code = code_by_guid.get(exec_key, "")
        source = "ПодразделениеИсполнитель_Key"
        if not code:
            code = guid_names.get(exec_key, exec_key[:8])
    elif assignee_key and assignee_key != EMPTY:
        code = code_by_guid.get(assignee_key, "")
        source = "КомуПодразделениеСсылка_Key"
        if not code:
            code = guid_names.get(assignee_key, assignee_key[:8])

    name = name_by_code.get(code, "") if code.startswith("00-") else guid_names.get(
        (exec_key or assignee_key), ""
    )
    if not name and code.startswith("00-"):
        name = code
    return {
        "department_code": code if code.startswith("00-") else (code_by_guid.get(exec_key) or code_by_guid.get(assignee_key) or ""),
        "department_label": name or code or "(пусто)",
        "destination_source": source or "(нет)",
        "Кому": komu,
        "ПодразделениеИсполнитель_Key": doc.get("ПодразделениеИсполнитель_Key"),
        "КомуПодразделениеСсылка_Key": doc.get("КомуПодразделениеСсылка_Key"),
        "Направление": doc.get("Направление"),
        "ПлательщикНаправление": doc.get("ПлательщикНаправление"),
    }


def fetch_guid_names(client: httpx.Client, base: str, guids: set[str]) -> dict[str, str]:
    names: dict[str, str] = {}
    # try Catalog_СтруктураПредприятия / Catalog_ПодразделенияОрганизаций
    for ent in (
        "Catalog_СтруктураПредприятия",
        "Catalog_ПодразделенияОрганизаций",
        "Catalog_CRM_Подразделения",
    ):
        remaining = [g for g in guids if g not in names and g != EMPTY]
        for g in remaining[:80]:
            try:
                item = get_json(client, f"{base}{quote(ent)}(guid'{g}')?$format=json")
                names[g] = item.get("Description") or item.get("Code") or g
            except Exception:
                continue
        if len(names) >= len([g for g in guids if g != EMPTY]) * 0.5:
            break
    return names


def sender_domain(email: str) -> str:
    email = (email or "").strip().lower()
    if "@" not in email:
        return ""
    return email.split("@", 1)[1]


def subject_pattern(text: str) -> str:
    t = (text or "").lower()
    t = re.sub(r"\d+", "#", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t[:80]


def load_agent_rows(engine, numbers: list[str]) -> dict[str, dict]:
    if not numbers:
        return {}
    out: dict[str, dict] = {}
    chunk = 50
    with engine.connect() as conn:
        for i in range(0, len(numbers), chunk):
            part = numbers[i : i + chunk]
            rows = conn.execute(
                text(
                    """
                    SELECT
                        e.erp_document_number,
                        e.subject,
                        e.sender_email,
                        e.mailbox,
                        e.department_id,
                        e.department_name,
                        e.dept_confidence,
                        e.status,
                        e.summary_ru,
                        e.message_id,
                        e.id::text AS email_id
                    FROM email_messages e
                    WHERE e.erp_document_number = ANY(:nums)
                    """
                ),
                {"nums": part},
            ).mappings().all()
            for r in rows:
                row = dict(r)
                row["from_address"] = row.get("sender_email")
                row["routing_confidence"] = row.get("dept_confidence")
                row["summary"] = row.get("summary_ru")
                out[str(row["erp_document_number"])] = row

        for _num, row in list(out.items()):
            eid = row.get("email_id")
            if not eid:
                row["attachments"] = []
                continue
            atts = conn.execute(
                text(
                    """
                    SELECT filename, mime_type, size_bytes
                    FROM email_attachments
                    WHERE message_id = CAST(:eid AS uuid)
                    ORDER BY filename
                    LIMIT 20
                    """
                ),
                {"eid": eid},
            ).mappings().all()
            row["attachments"] = [dict(a) for a in atts]
    return out


def fetch_odata_attach_names(client: httpx.Client, base: str, ref: str) -> list[str]:
    # try navigation
    for nav in (
        "ПрисоединенныеФайлы",
        "Файлы",
    ):
        try:
            url = f"{base}{quote(DOC)}(guid'{ref}')/{quote(nav)}?$format=json&$top=30&$select=Description,FileName"
            items = get_json(client, url).get("value", [])
            names = []
            for it in items:
                names.append(it.get("Description") or it.get("FileName") or "")
            if names:
                return [n for n in names if n]
        except Exception:
            continue
    # catalog by owner
    try:
        ent = "Catalog_ТД_ВходящаяКорреспонденцияПрисоединенныеФайлы"
        flt = f"ВладелецФайла_Key eq guid'{ref}'"
        url = f"{base}{quote(ent)}?$format=json&$filter={quote(flt)}&$top=30"
        items = get_json(client, url).get("value", [])
        return [it.get("Description") or it.get("FileName") or "" for it in items if it.get("Description") or it.get("FileName")]
    except Exception:
        return []


def propose_improvements(docs: list[dict], mismatches: list[dict], dept_counts: Counter) -> list[dict]:
    proposals = []
    # domain -> dest
    domain_dest: dict[str, Counter] = defaultdict(Counter)
    theme_dest: dict[str, Counter] = defaultdict(Counter)
    recipient_dest: dict[str, Counter] = defaultdict(Counter)

    for d in docs:
        dest = d["destination"]["department_code"] or d["destination"]["department_label"]
        dom = sender_domain(d.get("EmailОтправителяПисьма") or "")
        if dom:
            domain_dest[dom][dest] += 1
        theme = (d.get("ТемаСлужебнойЗаписки") or "").lower()
        for kw in (
            "акт сверки",
            "счёт",
            "счет",
            "упд",
            "претензи",
            "тендер",
            "запрос цен",
            "коммерческ",
            "гарантий",
            "ремонт",
            "поверк",
            "сертифик",
            "кадр",
            "вакан",
            "резюме",
            "договор",
            "спецификац",
            "накладн",
            "страхован",
            "логистик",
            "доставк",
            "приглашен",
            "конференц",
            "обучен",
            "маркетинг",
            "реклам",
        ):
            if kw in theme or kw in (d.get("Содержание") or "").lower():
                theme_dest[kw][dest] += 1
        to_addr = (d.get("EmailПолучателяПисьма") or "").lower().strip()
        if to_addr:
            recipient_dest[to_addr][dest] += 1

    # exact email from recipient patterns where Акинина consistently routes
    for email, ctr in sorted(recipient_dest.items(), key=lambda x: -sum(x[1].values())):
        if sum(ctr.values()) < 3:
            continue
        top_code, top_n = ctr.most_common(1)[0]
        if not str(top_code).startswith("00-"):
            continue
        share = top_n / sum(ctr.values())
        if share >= 0.7:
            proposals.append(
                {
                    "priority": 1 if share >= 0.9 and top_n >= 5 else 2,
                    "type": "exact_email",
                    "email": email,
                    "suggested_code": top_code,
                    "evidence_count": top_n,
                    "total_for_email": sum(ctr.values()),
                    "share": round(share, 2),
                    "rationale": f"Акинина в {top_n}/{sum(ctr.values())} писем на {email} отправила в {top_code}",
                }
            )

    for dom, ctr in sorted(domain_dest.items(), key=lambda x: -sum(x[1].values())):
        if sum(ctr.values()) < 3:
            continue
        top_code, top_n = ctr.most_common(1)[0]
        if not str(top_code).startswith("00-"):
            continue
        share = top_n / sum(ctr.values())
        if share >= 0.75 and top_n >= 3:
            proposals.append(
                {
                    "priority": 2,
                    "type": "exact_email_or_domain_content",
                    "sender_domain": dom,
                    "suggested_code": top_code,
                    "evidence_count": top_n,
                    "total_for_domain": sum(ctr.values()),
                    "share": round(share, 2),
                    "rationale": f"Домен {dom}: {top_n}/{sum(ctr.values())} → {top_code}",
                }
            )

    for kw, ctr in sorted(theme_dest.items(), key=lambda x: -sum(x[1].values())):
        if sum(ctr.values()) < 3:
            continue
        top_code, top_n = ctr.most_common(1)[0]
        if not str(top_code).startswith("00-"):
            continue
        share = top_n / sum(ctr.values())
        if share >= 0.7:
            proposals.append(
                {
                    "priority": 2 if top_n >= 5 else 3,
                    "type": "content_rules",
                    "pattern": kw,
                    "suggested_code": top_code,
                    "evidence_count": top_n,
                    "total_for_pattern": sum(ctr.values()),
                    "share": round(share, 2),
                    "rationale": f"Тема/содержание «{kw}»: {top_n}/{sum(ctr.values())} → {top_code}",
                }
            )

    if mismatches:
        mm_pairs = Counter(
            (m.get("agent_department_id"), m.get("akinina_department_code")) for m in mismatches
        )
        for (agent_d, ak_d), n in mm_pairs.most_common(10):
            if not ak_d or not str(ak_d).startswith("00-"):
                continue
            proposals.append(
                {
                    "priority": 1 if n >= 3 else 2,
                    "type": "correction_pattern_or_confidence_gate",
                    "agent_department_id": agent_d,
                    "akinina_department_code": ak_d,
                    "mismatch_count": n,
                    "rationale": f"Агент → {agent_d}, Акинина → {ak_d} ({n} расхождений). Добавить correction/правило или снизить confidence.",
                    "sample_numbers": [m["Number"] for m in mismatches if m.get("agent_department_id") == agent_d and m.get("akinina_department_code") == ak_d][:5],
                }
            )

    # empty destination warning
    empty_n = sum(1 for d in docs if d["destination"]["destination_source"] == "(нет)" or d["destination"]["department_label"] == "(пусто)")
    if empty_n:
        proposals.append(
            {
                "priority": 3,
                "type": "data_quality_note",
                "empty_destination_count": empty_n,
                "rationale": f"У {empty_n} документов Акининой не заполнен Кому/ПодразделениеИсполнитель — возможно черновики или маршрут через задачи.",
            }
        )

    # top departments as RAG boost
    for code, n in dept_counts.most_common(8):
        if not str(code).startswith("00-"):
            continue
        proposals.append(
            {
                "priority": 3,
                "type": "rag_keyword_boost",
                "suggested_code": code,
                "count": n,
                "rationale": f"Частый целевой отдел Акининой ({n} писем) — проверить keywords/RAG для {code}",
            }
        )

    proposals.sort(key=lambda p: (p.get("priority", 9), -int(p.get("evidence_count") or p.get("mismatch_count") or p.get("count") or 0)))
    # dedupe similar
    seen = set()
    uniq = []
    for p in proposals:
        key = (p.get("type"), p.get("email") or p.get("sender_domain") or p.get("pattern") or p.get("suggested_code"), p.get("agent_department_id"), p.get("akinina_department_code"))
        if key in seen:
            continue
        seen.add(key)
        uniq.append(p)
    return uniq[:25]


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    settings = get_settings()
    base = settings.odata_base_url.rstrip("/") + "/"
    auth = (settings.odata_username, settings.odata_password)
    code_by_guid, name_by_code = load_dept_maps(settings)

    with httpx.Client(auth=auth) as client:
        flt = f"Ответственный_Key eq guid'{USER_KEY}'"
        url = (
            f"{base}{quote(DOC)}?$format=json"
            f"&$filter={quote(flt)}"
            f"&$orderby=Date desc&$top={TOP_N}"
        )
        raw_docs = get_json(client, url).get("value", [])

        guids = set()
        for d in raw_docs:
            for k in ("ПодразделениеИсполнитель_Key", "КомуПодразделениеСсылка_Key"):
                g = (d.get(k) or "").lower()
                if g and g != EMPTY:
                    guids.add(g)
        guid_names = fetch_guid_names(client, base, guids)
        # merge known guids from department keys into names
        for code, guid in json.loads((ROOT / "data" / "odata_department_keys.json").read_text(encoding="utf-8")).items():
            g = guid.lower()
            if g in guids and g not in guid_names:
                guid_names[g] = name_by_code.get(code, code)

        docs = []
        for d in raw_docs:
            dest = resolve_dept(d, code_by_guid, name_by_code, guid_names)
            # resolve name if we have code
            if dest["department_code"] and not dest["department_label"]:
                dest["department_label"] = name_by_code.get(dest["department_code"], dest["department_code"])
            elif dest["department_code"]:
                dest["department_label"] = name_by_code.get(dest["department_code"], dest["department_label"])

            # Enrich names via guid_names for label
            if dest["department_label"] in (dest["department_code"], "(пусто)", "") or len(dest["department_label"]) < 3:
                for key_name in ("ПодразделениеИсполнитель_Key", "КомуПодразделениеСсылка_Key"):
                    g = (d.get(key_name) or "").lower()
                    if g in guid_names:
                        dest["department_label"] = guid_names[g]
                        break

            docs.append(
                {
                    "Number": d.get("Number"),
                    "Date": d.get("Date"),
                    "Ref_Key": d.get("Ref_Key"),
                    "Статус": d.get("Статус"),
                    "ТемаСлужебнойЗаписки": d.get("ТемаСлужебнойЗаписки"),
                    "Содержание": (d.get("Содержание") or "")[:500],
                    "Партнер": d.get("Партнер"),
                    "EmailОтправителяПисьма": d.get("EmailОтправителяПисьма"),
                    "EmailПолучателяПисьма": d.get("EmailПолучателяПисьма"),
                    "Организация_Key": d.get("Организация_Key"),
                    "destination": dest,
                    "odata_attachments": [],
                }
            )

        # OData attachments for a sample of interesting docs (first 25)
        for item in docs[:25]:
            try:
                item["odata_attachments"] = fetch_odata_attach_names(client, base, item["Ref_Key"])
            except Exception:
                item["odata_attachments"] = []

    # Agent DB join
    engine = create_engine(settings.database_url)
    agent_map = load_agent_rows(engine, [d["Number"] for d in docs if d.get("Number")])

    mismatches = []
    joinable = 0
    for d in docs:
        ag = agent_map.get(d["Number"] or "")
        d["agent"] = None
        if not ag:
            continue
        joinable += 1
        ak_code = d["destination"]["department_code"]
        agent_code = ag.get("department_id") or ""
        d["agent"] = {
            "department_id": agent_code,
            "department_name": ag.get("department_name"),
            "subject": ag.get("subject"),
            "from_address": ag.get("from_address"),
            "routing_confidence": ag.get("routing_confidence"),
            "status": ag.get("status"),
            "summary": (ag.get("summary") or ag.get("body_preview") or "")[:400],
            "attachments": ag.get("attachments") or [],
        }
        if ak_code and agent_code and ak_code != agent_code:
            mismatches.append(
                {
                    "Number": d["Number"],
                    "subject_short": (d.get("ТемаСлужебнойЗаписки") or ag.get("subject") or "")[:100],
                    "akinina_department_code": ak_code,
                    "akinina_department_label": d["destination"]["department_label"],
                    "agent_department_id": agent_code,
                    "agent_department_name": ag.get("department_name"),
                    "from_address": ag.get("from_address") or d.get("EmailОтправителяПисьма"),
                }
            )

    dept_counts: Counter = Counter()
    for d in docs:
        code = d["destination"]["department_code"] or d["destination"]["department_label"]
        dept_counts[code] += 1

    domain_counts = Counter(sender_domain(d.get("EmailОтправителяПисьма") or "") for d in docs)
    recipient_counts = Counter((d.get("EmailПолучателяПисьма") or "").lower() for d in docs)
    theme_patterns = Counter(subject_pattern(d.get("ТемаСлужебнойЗаписки") or "") for d in docs)

    # examples: mix of mismatches + top destinations diversity
    examples = []
    used = set()
    for m in mismatches[:8]:
        d = next(x for x in docs if x["Number"] == m["Number"])
        examples.append(_example_row(d))
        used.add(d["Number"])
    for d in docs:
        if d["Number"] in used:
            continue
        if d["destination"]["department_code"]:
            examples.append(_example_row(d))
            used.add(d["Number"])
        if len(examples) >= 15:
            break
    # fill
    for d in docs:
        if len(examples) >= 12:
            break
        if d["Number"] not in used:
            examples.append(_example_row(d))
            used.add(d["Number"])

    proposals = propose_improvements(docs, mismatches, dept_counts)

    # department stats with names
    dept_stats = []
    for code, n in dept_counts.most_common():
        label = name_by_code.get(code, code)
        if code in name_by_code:
            label = name_by_code[code]
        dept_stats.append({"department_code": code, "department_name": label, "count": n})

    report = {
        "meta": {
            "user_name": USER_NAME,
            "user_ref_key": USER_KEY,
            "identification_field": "Ответственный_Key",
            "identification_notes": (
                "Единственное пользовательское поле Document_ТД_ВходящаяКорреспонденция, "
                "которое стабильно ссылается на Catalog_Пользователи Акининой. "
                "Автор_Key / Зарегистрировал_Key / Изменил_Key в OData отсутствуют. "
                "Поле Автор (строка OpenType) в выборках не отдаётся. "
                f"В последних 500 документах {sum(1 for _ in [])} — см. sample_size."
            ),
            "sample_size": len(docs),
            "date_from": docs[-1]["Date"] if docs else None,
            "date_to": docs[0]["Date"] if docs else None,
            "agent_joinable": joinable,
            "agent_mismatches": len(mismatches),
            "destination_field_priority": [
                "Кому (код 00-xxxxxx)",
                "ПодразделениеИсполнитель_Key",
                "КомуПодразделениеСсылка_Key",
            ],
        },
        "department_stats": dept_stats,
        "destination_source_stats": dict(Counter(d["destination"]["destination_source"] for d in docs)),
        "top_sender_domains": [
            {"domain": d, "count": n} for d, n in domain_counts.most_common(20) if d
        ],
        "top_recipient_mailboxes": [
            {"email": e, "count": n} for e, n in recipient_counts.most_common(15) if e
        ],
        "top_subject_patterns": [
            {"pattern": p, "count": n} for p, n in theme_patterns.most_common(25) if p
        ],
        "examples": examples,
        "mismatches": mismatches[:40],
        "improvement_proposals": proposals,
        "documents": [
            {
                "Number": d["Number"],
                "Date": d["Date"],
                "theme": (d.get("ТемаСлужебнойЗаписки") or "")[:160],
                "partner": d.get("Партнер"),
                "from": d.get("EmailОтправителяПисьма"),
                "to": d.get("EmailПолучателяПисьма"),
                "destination": d["destination"],
                "agent_department_id": (d.get("agent") or {}).get("department_id"),
                "content_preview": (d.get("Содержание") or "")[:220],
                "odata_attachments": d.get("odata_attachments") or [],
                "agent_attachments": (d.get("agent") or {}).get("attachments") or [],
            }
            for d in docs
        ],
    }

    # fix identification_notes
    report["meta"]["identification_notes"] = (
        "Действия Акининой идентифицируются по Ответственный_Key = "
        f"{USER_KEY} (Catalog_Пользователи «{USER_NAME}»). "
        "Автор_Key / Зарегистрировал_Key / Изменил_Key в метаданных документа нет. "
        "Строковое поле Автор через OData не возвращается. "
        "Задачи Task_ЗадачаИсполнителя.Исполнитель также встречаются, но основной объём — Ответственный_Key."
    )

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "written": str(OUT),
        "sample_size": len(docs),
        "date_from": report["meta"]["date_from"],
        "date_to": report["meta"]["date_to"],
        "joinable": joinable,
        "mismatches": len(mismatches),
        "top_departments": dept_stats[:15],
        "destination_sources": report["destination_source_stats"],
        "proposals_count": len(proposals),
        "examples": examples,
    }, ensure_ascii=False, indent=2))


def _example_row(d: dict) -> dict:
    ag = d.get("agent") or {}
    return {
        "Number": d["Number"],
        "Date": d["Date"],
        "subject_short": (d.get("ТемаСлужебнойЗаписки") or ag.get("subject") or "")[:120],
        "from": d.get("EmailОтправителяПисьма") or ag.get("from_address"),
        "to": d.get("EmailПолучателяПисьма"),
        "akinina_department": d["destination"]["department_code"] or d["destination"]["department_label"],
        "akinina_department_name": d["destination"]["department_label"],
        "agent_department": ag.get("department_id"),
        "agent_department_name": ag.get("department_name"),
        "match": (
            bool(ag.get("department_id"))
            and ag.get("department_id") == d["destination"]["department_code"]
        ),
        "attachments": (ag.get("attachments") or d.get("odata_attachments") or [])[:5],
    }


if __name__ == "__main__":
    main()
